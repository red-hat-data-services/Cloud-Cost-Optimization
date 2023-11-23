import json
import time

import boto3
import os


class oc_cluster:
    def __init__(self, cluster_detail, ocm_account):
        details = cluster_detail.split(' ')
        details = [detail for detail in details if detail]
        self.id = details[0]
        self.name = details[1]
        self.api_url = details[2]
        self.ocp_version = details[3]
        self.type = details[4]
        self.hcp = details[5]
        self.cloud_provider = details[6]
        self.region = details[7]
        self.status = details[8]
        self.nodes = []
        self.hibernate_error = ''
        self.ocm_account = ocm_account
def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_instances_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': [current_state]}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=1000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']: instance for instance in
               ec2_map}
    print(region, len(ec2_map))
    return ec2_map

def get_all_instances(ec2_instances, current_state):
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        ec2_instances[region] = get_instances_for_region(region, current_state)


def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')

def worker_node_belongs_to_the_hcp_cluster(ec2_instance:dict, cluster_name:str):
    result = False
    for tag in ec2_instance['Tags']:
        if tag['Key'] == 'api.openshift.com/name' and tag['Value'] == cluster_name:
            result = True
            break
    return result
def check_if_given_tag_exists(tag_name, tags:list[dict]):
    print(tags)
    result = False
    for tag in tags:
        if tag['Key'] == tag_name:
            result = True
            break
    return result

def delete_volume(volume_id, region):
    ec2_client = boto3.client('ec2', region_name=region)
    for attempt in range(7):
        try:
            ec2_client.delete_volume(VolumeId=volume_id)
            print(f'Deleted the volume {volume_id}')
        except:
            time.sleep(5)
def hybernate_hypershift_cluster(cluster:oc_cluster, ec2_map:dict):
    # ec2_map = ec2_instances[cluster.region]
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes if worker_node_belongs_to_the_hcp_cluster(ec2_map[worker_node], cluster.name)]
    if len(InstanceIds) > 0:
        print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
        worker_count = len(InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
        wait_for_rosa_cluster_to_be_hibernated(cluster, worker_count)
        # detach and delete the volumes
        filters = [{'Name': 'attachment.instance-id', 'Values': InstanceIds}]
        attached_volumes = ec2_client.describe_volumes(Filters=filters)
        attached_volumes = [attachment for volume in attached_volumes['Volumes'] for attachment in volume['Attachments']
                            if attachment['DeleteOnTermination'] == True and not check_if_given_tag_exists(
                'KubernetesCluster', volume['Tags'])]
        print('attached_volumes', attached_volumes)
        for volume in attached_volumes:
            print(f'detaching the volume {volume["VolumeId"]}')
            ec2_client.detach_volume(Device=volume['Device'], InstanceId=volume['InstanceId'], VolumeId=volume['VolumeId'])
        for volume in attached_volumes:
            print(f'deleting the volume {volume["VolumeId"]}')
            delete_volume(volume['VolumeId'], cluster.region)

        print(f'Done hibernating the cluster {cluster.name}')
    else:
        print(f'Cluster {cluster.name} is already hibernated.')

def get_instance_status(cluster:oc_cluster, InstanceIds:list):
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    ec2_map = ec2_client.describe_instances(InstanceIds=InstanceIds)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    status_map = {ec2['InstanceId']:ec2['State']['Name'] for ec2 in ec2_map}
    return status_map

def wait_for_rosa_cluster_to_be_hibernated(cluster:oc_cluster, worker_count:int):
    time.sleep(5)
    ec2_map = get_instances_for_region(cluster.region, 'stopped')
    InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-') and worker_node_belongs_to_the_hcp_cluster(ec2_map[ec2_name], cluster.name)]
    while len(InstanceIds) < worker_count:
        print('Worker nodes stopping, please wait...')
        time.sleep(5)
        ec2_map = get_instances_for_region(cluster.region, 'stopped')
        InstanceIds = [ec2_map[ec2_name]['InstanceId'] for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-') and worker_node_belongs_to_the_hcp_cluster(ec2_map[ec2_name], cluster.name)]

    status_map = get_instance_status(cluster, InstanceIds)
    while set(status_map.values()) != set(['stopped']):
        print('Worker nodes stopping, please wait...')
        time.sleep(5)
        status_map = get_instance_status(cluster, InstanceIds)
def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def hibernate_cluster(cluster: oc_cluster):
    run_command(f'script/./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'script/./resume_cluster.sh {cluster.ocm_account} {cluster.id}')
def main():
    ec2_instances = {}
    get_all_instances(ec2_instances, 'running')

    clusters = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    clusters_to_hibernate = [cluster for cluster in clusters if (cluster.type == 'osd' or (cluster.type == 'rosa')) and cluster.status == 'ready']
    print('cluster to hibernate')
    for cluster in clusters_to_hibernate:
        print(cluster.name, cluster.type)

    hibernated_clusters = []
    for cluster in clusters_to_hibernate:
        print('starting with', cluster.name, cluster.type)
        if cluster.hcp == "false":
            hibernate_cluster(cluster)
            print("OSD or ROSA Classic - ", cluster.name)
        else:
            hybernate_hypershift_cluster(cluster, ec2_instances[cluster.region])
            print("Hypershift cluster - ", cluster.name)
        hibernated_clusters.append(cluster.__dict__)
        # print(f'Hibernated {cluster.name}')
    hibernated_json = json.dumps(hibernated_clusters, indent=4)
    print(hibernated_json)
    open('hibernated_latest.json', 'w').write(hibernated_json)
    s3 = boto3.client('s3')
    try:
        s3.upload_file('hibernated_latest.json', 'rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json')
    except Exception as e:
        print(e)


if __name__ == '__main__':
    main()