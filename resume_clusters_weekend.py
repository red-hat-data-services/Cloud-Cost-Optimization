import json
import boto3
import os

# need to sync the list with latest status, and resume it only if status is Hibernating

class oc_cluster:
    def __init__(self, cluster_detail):
        self.id = cluster_detail['id']
        self.name = cluster_detail['name']
        self.api_url = cluster_detail['api_url']
        self.ocp_version = cluster_detail['ocp_version']
        self.type = cluster_detail['type']
        self.hcp = cluster_detail['hcp']
        self.cloud_provider = cluster_detail['cloud_provider']
        self.region = cluster_detail['region']
        self.status = cluster_detail['status']
        self.resume_error = ''
        self.ocm_account = cluster_detail['ocm_account']
def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_cluster_list(ocm_account:str):
    run_command(f'./get_all_cluster_details.sh {ocm_account}')
def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def get_last_hibernated():
    s3 = boto3.client('s3')
    s3.download_file('rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json', 'hibernated_latest.json')

def resume_hypershift_cluster(cluster:oc_cluster, ec2_instances:dict):
    ec2_map = ec2_instances[cluster.region]
    print([name for name in ec2_map])
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-workers-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes]
    print(f'Starting Worker Instances of cluster {cluster.name}', InstanceIds)
    ec2_client.start_instances(InstanceIds=InstanceIds)

def hibernate_cluster(cluster: oc_cluster):
    run_command(f'./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'./resume_cluster.sh {cluster.ocm_account} {cluster.id}')

def get_instances_for_region(region):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': ['stopped']}]
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

def main():
    ec2_instances = {}
    get_all_instances(ec2_instances)
    get_last_hibernated()
    clusters_to_resume = []
    clusters = json.load(open('hibernated_latest.json'))
    for cluster in clusters:
        clusters_to_resume.append(oc_cluster(cluster))
    resumed_clusters = []
    for cluster in clusters_to_resume:
        print('starting with', cluster.name, cluster.type)
        if cluster.hcp == "false":
            resume_cluster(cluster)
        else:
            resume_hypershift_cluster(cluster, ec2_instances)
        resumed_clusters.append(cluster.__dict__)
        # print(f'Hibernated {cluster.name}')

    print(json.dumps(resumed_clusters, indent=4))


if __name__ == '__main__':
    main()