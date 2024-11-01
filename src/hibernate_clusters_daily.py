import json
import boto3
import time, datetime
import os
import smartsheet
import re

class oc_cluster:
    def __init__(self, cluster_detail, ocm_account):
        details = cluster_detail.split(' ')
        details = [detail for detail in details if detail]
        self.id = details[0]
        self.name = details[1]
        self.internal_name = details[1]
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

def get_ipi_cluster_name(cluster:oc_cluster):
    if cluster.name.count('-') == 4:
        try:
            url = run_command(f'ocm describe cluster {cluster.id} | grep "Console URL:"')
            url = url.replace('Console URL:', '').strip()
            result = re.search(r"^https:\/\/console-openshift-console.apps.(.*).ocp2.odhdev.com$", url)
            if result:
                cluster.internal_name = result.group(1)
        except:
            print(f'could not retrieve internal name for IPI cluster {cluster.name}, the cluster seems stale or non-existent')

def get_all_cluster_details(ocm_account:str, clusters:dict):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        cluster = oc_cluster(cluster_detail, ocm_account)
        if cluster.type == 'ocp':
            get_ipi_cluster_name(cluster)
        if cluster.cloud_provider == 'aws' and (
                cluster.type != 'ocp' or (cluster.type == 'ocp' and cluster.name != cluster.internal_name)):
            clusters.append(cluster)
    # clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

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

def get_clusters_from_smartsheet():
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    inactive_hours_start_index, inactive_hours_end_index = 5, 6
    sheet = smart.Sheets.get_sheet(sheed_id)

    # get existing data
    smartsheet_data = {
            row.cells[0].value: [
                row.cells[inactive_hours_start_index].value, row.cells[inactive_hours_end_index].value
                ] for row in sheet.rows
            }
    return smartsheet_data

def hibernate_cluster(cluster: oc_cluster):
    run_command(f'script/./hybernate_cluster.sh {cluster.ocm_account} {cluster.id}')

def good_time_to_hibernate_cluster(cluster):
    
    inactive_hours_start = cluster.inactive_hours_start
    inactive_hours_end = cluster.inactive_hours_end

    # if inactive hours start is not set, do not hibernate cluster
    if not inactive_hours_start:
        return False

    # checking to see if smartsheet time entries are missing the seconds part
    if inactive_hours_start.count(':') == 1:
        inactive_hours_start += ':00'
    
    if inactive_hours_end.count(':') == 1:
        inactive_hours_end += ':00'

    # converting to time objects
    try:
        inactive_hours_start = datetime.datetime.strptime(inactive_hours_start, '%H:%M:%S')
    
    # if the inactive hours start is misconfigured, default to hibernating cluster immediately
    except ValueError:
        print(f'error parsing inactive hours start on cluster {cluster.name}, defaulting to hibernate')
        print(f'inactive hours start: {cluster.inactive_hours_start}') 
        return False

    # if inactive hours end is misconfigured or blank, default to one day after start, which will cause it to be ignored
    try:
        inactive_hours_end = datetime.datetime.strptime(inactive_hours_end, '%H:%M:%S')
    except ValueError:
        inactive_hours_end = inactive_hours_start + datetime.timedelta(days=1)

    # start, end, and current are all relative to epoch

    current_utc_time = datetime.datetime.strptime(
            datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S'),'%H:%M:%S')

    if inactive_hours_end < inactive_hours_start:
        return current_utc_time <= inactive_hours_end or current_utc_time >= inactive_hours_start

    return inactive_hours_start <= current_utc_time <= inactive_hours_end 

def worker_node_belongs_to_the_ipi_cluster(ec2_instance:dict, cluster_name:str):
    tags = {tag['Key']:tag['Value'] for tag in ec2_instance['Tags']}
    result = 'red-hat-clustertype' not in tags and 'api.openshift.com/name' not in tags
    for key, value in tags.items():
        if key.startswith(f'kubernetes.io/cluster/{cluster_name}-') and value == 'owned':
            result = result and True
            break
    return result
def hibernate_ipi_cluster(cluster:oc_cluster, ec2_map:dict):

    result = False
    worker_nodes = [ec2_name for ec2_name in ec2_map if ec2_name.startswith(f'{cluster.internal_name}-')]
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    InstanceIds = [ec2_map[worker_node]['InstanceId'] for worker_node in worker_nodes if worker_node_belongs_to_the_ipi_cluster(ec2_map[worker_node], cluster.internal_name)]
    if len(InstanceIds) > 0:
        print(f'Stopping Worker Instances of cluster {cluster.name}', InstanceIds)
        worker_count = len(InstanceIds)
        ec2_client.stop_instances(InstanceIds=InstanceIds)
        print(f'Started hibernating the cluster {cluster.name}')
        result = True
    else:
        print(f'Cluster {cluster.name} is already hibernated.')
    return result

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

    # updating inactive_hours_start and end for each cluster based on smartsheet
    for cluster in clusters:
        if cluster.id not in smartsheet_data:
            print(f'{cluster.name} ({cluster.id}) not found in smartsheet data')
            continue
        
        smartsheet_cluster_info = smartsheet_data[cluster.id]
       
        if smartsheet_cluster_info[0]:
            cluster.inactive_hours_start = smartsheet_cluster_info[0]
        else:
            print(f'Start time not found for {cluster.name}')
        
        if smartsheet_cluster_info[1]:
            cluster.inactive_hours_end = smartsheet_cluster_info[1]
        else:
            print(f'End time not found for {cluster.name}')


    hibernated_clusters = []
    no_action_clusters = []

    for cluster in clusters:
    
        if good_time_to_hibernate_cluster(cluster):
            if cluster.hcp == "false":
                if cluster.type == 'ocp':
                    print("Hibernating IPI Cluster - ", cluster.name)
                    hibernate_ipi_cluster(cluster, ec2_instances[cluster.region])
                else:
                    print("Hibernating OSD or ROSA Classic Cluster - ", cluster.name)
                    hibernate_cluster(cluster)
            else:
                hybernate_hypershift_cluster(cluster, ec2_instances[cluster.region])
                print("Hibernating Hypershift Cluster - ", cluster.name)
            hibernated_clusters.append(cluster.__dict__)
        else:
            no_action_clusters.append(cluster.__dict__)

    print("The following clusters were hibernated: ")
    print(json.dumps(hibernated_clusters, indent=4))

    print("No action taken for the following cluster:")
    print(json.dumps(no_action_clusters, indent=4))

if __name__ == '__main__':
    main()
