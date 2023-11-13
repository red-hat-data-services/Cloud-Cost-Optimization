import json
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

def get_instances_for_region(region):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': ['running']}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=1000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']: instance for instance in
               ec2_map}
    print(region, len(ec2_map))
    return ec2_map

def get_all_instances(ec2_instances):
    client = boto3.client('ec2')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        ec2_instances[region] = get_instances_for_region(region)


def get_cluster_list(ocm_account:str):
    run_command(f'./get_all_cluster_details.sh {ocm_account}')

def hybernate_hypershift_cluster(cluster:oc_cluster, ec2_instances:dict):
    ec2_map = ec2_instances[cluster.region]
    print([name for name in ec2_map])
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-workers-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes]
    print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
    ec2_client.stop_instances(InstanceIds=InstanceIds)



def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def hibernate_cluster(cluster: oc_cluster):
    run_command(f'./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'./resume_cluster.sh {cluster.ocm_account} {cluster.id}')
def main():
    ec2_instances = {}
    get_all_instances(ec2_instances)

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
            # hibernate_cluster(cluster)
            pass
        elif cluster.name == 'dchouras-hcp':
            hybernate_hypershift_cluster(cluster, ec2_instances)
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