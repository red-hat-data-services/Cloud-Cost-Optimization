import json
import boto3
import time, datetime
import os
import smartsheet

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
        self.inactive_hours_start = None
        self.inactive_hours_end = None

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
def worker_node_belongs_to_the_hcp_cluster(ec2_instance:dict, cluster_name:str):
    result = False
    for tag in ec2_instance['Tags']:
        if tag['Key'] == 'api.openshift.com/name' and tag['Value'] == cluster_name:
            result = True
            break
    return result
def hybernate_hypershift_cluster(cluster:oc_cluster, ec2_map:dict):
    # ec2_map = ec2_instances[cluster.region]
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes if worker_node_belongs_to_the_hcp_cluster(ec2_map[worker_node], cluster.name)]
    if len(InstanceIds) > 0:
        print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
        worker_count = len(InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
        print(f'Started hibernating the cluster {cluster.name}')
        time.sleep(5)
    else:
        print(f'Cluster {cluster.name} is already hibernated.')

def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')

def run_command(command):
    print(command)
    output = os.popen(command).read()
    print(output)
    return output

def hybernate_hypershift_cluster(cluster:oc_cluster, ec2_map:dict):
    # ec2_map = ec2_instances[cluster.region]
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.name}-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes if worker_node_belongs_to_the_hcp_cluster(ec2_map[worker_node], cluster.name)]
    if len(InstanceIds) > 0:
        print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
        worker_count = len(InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
        print(f'Started hibernating the cluster {cluster.name}')
        time.sleep(5)
    else:
        print(f'Cluster {cluster.name} is already hibernated.')

def get_clusters_from_smartsheet():
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    inactive_hours_start_index, inactive_hours_end_index = 5, 6
    sheet = smart.Sheets.get_sheet(sheed_id)

    # get existing data
    smartsheet_data = {row.cells[0].value: [row.cells[inactive_hours_start_index].value, row.cells[inactive_hours_start_index].value] for row in sheet.rows}
    return smartsheet_data

def hibernate_cluster(cluster: oc_cluster):
    run_command(f'script/./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def resume_cluster(cluster: oc_cluster):
    run_command(f'script/./resume_cluster.sh {cluster.ocm_account} {cluster.id}')
def main():
    ec2_instances = {}
    get_all_instances(ec2_instances, 'running')

    clusters:list[oc_cluster] = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)

    smartsheet_data = get_clusters_from_smartsheet()
    for cluster in clusters:
        if cluster.id in smartsheet_data:
            smartsheet_cluster_info = smartsheet_data[cluster.id]

            if smartsheet_cluster_info[0]:
                if smartsheet_cluster_info[0].count(':') < 1:
                    print(f'Invalid inactive_hours_start {smartsheet_cluster_info[0]} for cluster {cluster.name}')
                    continue
                if smartsheet_cluster_info[0].count(':') == 1:
                    smartsheet_cluster_info[0] += ':00'
                cluster.inactive_hours_start = time.strptime(smartsheet_cluster_info[0], '%H:%M:%S')

    hibernated_clusters = []
    for cluster in clusters:
        current_utc_time = time.strptime(datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S'),
                                         '%H:%M:%S')
        if cluster.inactive_hours_start and current_utc_time >= cluster.inactive_hours_start:
            if cluster.hcp == "false":
                # hibernate_cluster(cluster)
                print("OSD or ROSA Classic - ", cluster.name)
            else:
                # hybernate_hypershift_cluster(cluster, ec2_instances[cluster.region])
                print("Hypershift cluster - ", cluster.name)
            hibernated_clusters.append(cluster.__dict__)


    print(json.dumps(hibernated_clusters, indent=4))


if __name__ == '__main__':
    main()