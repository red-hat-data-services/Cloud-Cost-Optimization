import datetime
import json
import os
import re
import time
from enum import Enum
import boto3
import smartsheet


# === CLUSTER DATA CLASS ===========================================================================
class OcCluster:
    def __init__(self, cluster_detail, ocm_account=None):
        if isinstance(cluster_detail, dict):
            self.id = cluster_detail['id']
            self.name = cluster_detail['name']
            self.internal_name = cluster_detail.get('internal_name', cluster_detail['name'])
            self.api_url = cluster_detail['api_url']
            self.ocp_version = cluster_detail['ocp_version']
            self.type = cluster_detail['type']
            self.hcp = cluster_detail['hcp']
            self.cloud_provider = cluster_detail['cloud_provider']
            self.region = cluster_detail['region']
            self.status = cluster_detail['status']
            self.ocm_account = cluster_detail.get('ocm_account', ocm_account)
        else:
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
            self.ocm_account = ocm_account
        self.nodes = []
        self.hibernate_error = ''
        self.resume_error = ''
        self.inactive_hours_start = None
        self.inactive_hours_end = None
        self.creation_date = ''
        self.creator_name = ''
        self.creator_email = ''


# === EC2 INSTANCE CHECKS ===============================================================================
def worker_node_belongs_to_the_hcp_cluster(ec2_instance:dict, cluster_name:str) -> bool:
    """Check if an EC2 instance belongs to a specific HCP cluster """
    result = False
    for tag in ec2_instance['Tags']:
        if tag['Key'] == 'api.openshift.com/name' and tag['Value'] == cluster_name:
            result = True
            break
    return result


def worker_node_belongs_to_the_ipi_cluster(ec2_instance:dict, cluster_name:str) -> bool:
    """Check if an EC2 instance belongs to a specific IPI cluster """
    tags = {tag['Key']:tag['Value'] for tag in ec2_instance['Tags']}
    result = 'red-hat-clustertype' not in tags and 'api.openshift.com/name' not in tags
    for key, value in tags.items():
        if key.startswith(f'kubernetes.io/cluster/{cluster_name}-') and value == 'owned':
            result = result and True
            break
    return result


def get_instance_status(cluster, InstanceIds:list):
    """Get the status of all EC2 instances in a cluster's region"""
    ec2_client = boto3.client('ec2', region_name=cluster.region)
    ec2_map = ec2_client.describe_instances(InstanceIds=InstanceIds)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    status_map = {ec2['InstanceId']:ec2['State']['Name'] for ec2 in ec2_map}
    return status_map


def get_all_instances(ec2_instances, current_state):
    """Get all EC2 instances in a specific state"""
    client = boto3.client('ec2', region_name='us-east-1')
    regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
    for region in regions:
        ec2_instances[region] = get_instances_for_region(region, current_state)


def get_instances_for_region(region, current_state):
    """Return all EC2 instances for a given region"""
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': [current_state]}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=10_000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]

    # return a map of instances, keyed by their name tag
    tagged_instances_map = {}
    for instance in ec2_map:
        name_tags = [tag['Value'] for tag in instance.get("Tags", []) if tag['Key'] == 'Name']
        if name_tags:
            tagged_instances_map[name_tags[0]] = instance
    return tagged_instances_map


def get_instances_for_region_and_cluster_name(region, current_state, cluster_name):
    """Return all EC2 instances for a given region"""
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [
        {'Name': 'instance-state-name', 'Values': [current_state]},
        {"Name": "tag:api.openshift.com/name", 'Values': [cluster_name]}
    ]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=10_000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]

    # return a map of instances, keyed by their name tag
    tagged_instances_map = {}
    for instance in ec2_map:
        name_tags = [tag['Value'] for tag in instance.get("Tags", []) if tag['Key'] == 'Name']
        if name_tags:
            tagged_instances_map[name_tags[0]] = instance
    return tagged_instances_map


# === SCRIPT CALLERS ===============================================================================
def run_command(command):
    """Run a shell command"""
    output = os.popen(command).read()
    return output


def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')

# === IPI UTILITIES ================================================================================
def get_ipi_cluster_name(cluster):
    if cluster.name.count('-') == 4:
        try:
            url = run_command(f'ocm describe cluster {cluster.id} | grep "Console URL:"')
            url = url.replace('Console URL:', '').strip()
            result = re.search(r"^https:\/\/console-openshift-console.apps.(.*).ocp2.odhdev.com$", url)
            if result:
                cluster.internal_name = result.group(1)
        except:
            print(f'could not retrieve internal name for IPI cluster {cluster.name}, the cluster seems stale or non-existent')


# === VOLUME UTILITIES ==========================================================================
def delete_volume(volume_id, region):
    ec2_client = boto3.client('ec2', region_name=region)
    for attempt in range(7):
        try:
            ec2_client.delete_volume(VolumeId=volume_id)
            print(f'Deleted the volume {volume_id}', flush=True)
            return
        except:
            time.sleep(5)
    print(f"Failed to delete volume {volume_id}", flush=True)


# === AUTH =========================================================================================
def get_ocm_api_token():
    if not os.path.isfile('ocm_token.txt'):
        run_command(f'script/./get_ocm_token.sh')
    ocm_api_token = str(open('ocm_token.txt').read()).strip('\n')
    return ocm_api_token


# === STRING UTILS =================================================================================
def sanitize_cluster_name(cluster_name:str):
    if cluster_name.count('-') == 4:
        cluster_name = cluster_name[:28]
    return cluster_name


def check_if_given_tag_exists(tag_name, tags:list[dict]):
    result = False
    for tag in tags:
        if tag['Key'] == tag_name:
            result = True
            break
    return result


# === SMARTSHEET UTILS =============================================================================
class ClusterSmartsheetColumns(Enum):
    ID=0
    NAME=1
    STATUS=2
    TYPE=3
    IS_HCP=4
    INACTIVE_HOURS_START=5
    INACTIVE_HOURS_END=6
    OWNER=7
    AGE=8
    CREATION_DATE=9
    REGION=10
    PROVIDER=11
    OCM_ACCOUNT=12


def get_clusters_from_smartsheet():
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    sheet = smart.Sheets.get_sheet(sheed_id)

    # return requested data as a map of cluster ID -> {column_name: value}
    smartsheet_data = {}
    for row in sheet.rows:
        row_data = {column: row.cells[column.value].value for column in ClusterSmartsheetColumns}
        smartsheet_data[row_data[ClusterSmartsheetColumns.ID]] = row_data
    return smartsheet_data


def get_all_cluster_details(ocm_account:str, clusters:list):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        cluster = OcCluster(cluster_detail, ocm_account)
        if cluster.type == 'ocp':
            get_ipi_cluster_name(cluster)
        if cluster.cloud_provider == 'aws' and (
                cluster.type != 'ocp' or (cluster.type == 'ocp' and cluster.name != cluster.internal_name)):
            clusters.append(cluster)
    # clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']
    update_cluster_details(clusters)


def update_cluster_details(clusters:list[OcCluster]):
    for cluster in clusters:
        run_command(f'script/./get_cluster_details.sh {cluster.ocm_account} {cluster.id}')
        details = json.load(open(f'{cluster.id}_details.json'))
        cluster.creation_date = details['creation_date']
        cluster.creator_name = details['creator_name']
        if details['creator_email'] and details['creator_email'] != 'null':
            cluster.creator_email = details['creator_email']
            if '+' in cluster.creator_email:
                cluster.creator_email = get_original_email_address(cluster.creator_email)


def get_original_email_address(email:str):
    parts = email.split('@')
    original_prefix = parts[0].split('+')[0]
    return f'{original_prefix}@{parts[1]}'


# === TIME UTILS ===================================================================================
def within_two_hour_window_after(action_timestamp: str, default_decision: bool):
    """Checks if we're within a two-hour window AFTER the action timestamp """

    buffer_hours = 2
    buffer_seconds = buffer_hours * 60 * 60
    day_start_time = '00:00:00'
    day_end_time = '23:59:59'
    try:
        action_timestamp = datetime.datetime.strptime(action_timestamp, '%H:%M:%S')
    # if the action timestamp is misconfigured, return the default_decision
    except ValueError:
        return default_decision

    current_utc_time = datetime.datetime.strptime(datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S'),                                                      '%H:%M:%S')
    day_start_time = datetime.datetime.strptime(day_start_time, '%H:%M:%S')
    day_end_time = datetime.datetime.strptime(day_end_time, '%H:%M:%S')
    diff = (current_utc_time - action_timestamp).total_seconds()

    if diff < 0 and 24 - buffer_hours < action_timestamp.hour <= 24 and 0 <= current_utc_time.hour <= buffer_hours:
        diff = (day_end_time - action_timestamp).total_seconds() + (
                current_utc_time - day_start_time).total_seconds()

    return 0 <= diff <= buffer_seconds
