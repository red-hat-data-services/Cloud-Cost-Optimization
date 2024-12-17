import json
import time

import boto3
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
        self.ocm_account = ocm_account
        self.creation_date = ''
        self.creator_name = ''
        self.creator_email = ''

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

def get_all_cluster_details(ocm_account:str, clusters:list):
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
    update_cluster_details(clusters)


def update_cluster_details(clusters:list[oc_cluster]):
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



def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')

def run_command(command):
    output = os.popen(command).read()
    # print(output)
    return output

def build_cells(cluster: oc_cluster, column_map:dict):
    cells = []

    column_object = {}
    column_object['columnId'] = column_map['ID']
    column_object['value'] = cluster.id
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Name']
    column_object['value'] = cluster.name
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Status']
    column_object['value'] = cluster.status
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Type']
    column_object['value'] = cluster.type
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['HCP']
    column_object['value'] = cluster.hcp
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Cloud_Provider']
    column_object['value'] = cluster.cloud_provider
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Region']
    column_object['value'] = cluster.region
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['OCM_Account']
    column_object['value'] = cluster.ocm_account
    cells.append(column_object)

    if cluster.creator_name and cluster.creator_email:
        column_object = {}
        column_object['columnId'] = column_map['Owner']
        column_object['value'] = cluster.creator_email #json.dumps([{ 'email': cluster.creator_email, 'name': cluster.creator_name}])

        cells.append(column_object)

    if cluster.creation_date:
        column_object = {}
        column_object['columnId'] = column_map['CreatedOn']
        column_object['value'] = cluster.creation_date
        cells.append(column_object)

    return cells

def update_smartsheet_data(clusters:dict[oc_cluster]):
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    sheet = smart.Sheets.get_sheet(sheed_id)
    for column in sheet.columns:
        column_map[column.title] = column.id
    print(column_map)

    # process existing data
    existingRows = {row.cells[0].value: row.id for row in sheet.rows}
    existingClusterIds = [cluster.id for cluster in clusters]

    smartsheet_existing_data = []
    smartsheet_new_data = []
    smartsheet_deleted_data = []

    for cluster in clusters:
        rowObject = {}
        if cluster.id in existingRows:
            rowObject['id'] = existingRows[cluster.id]
            rowObject['cells'] = build_cells(cluster, column_map)
            smartsheet_existing_data.append(rowObject)
        else:
            rowObject['toBottom'] = True
            rowObject['cells'] = build_cells(cluster, column_map)
            smartsheet_new_data.append(rowObject)

    for cluster_id, row_id in existingRows.items():
        if cluster_id not in existingClusterIds:
            smartsheet_deleted_data.append(str(row_id))

    if smartsheet_existing_data:
        payload = json.dumps(smartsheet_existing_data, indent=4)
        print('Updating existing clusters', payload)
        response = smart.Passthrough.put(f'/sheets/{sheed_id}/rows', payload)
        # print(response)

    if smartsheet_new_data:
        payload = json.dumps(smartsheet_new_data, indent=4)
        print('Adding new clusters', payload)
        response = smart.Passthrough.post(f'/sheets/{sheed_id}/rows', payload)
        # print(response)
        payload = json.dumps({'sortCriteria': [{'columnId': column_map['Name'], 'direction': 'ASCENDING'}]})
        response_sort = smart.Passthrough.post(f'/sheets/{sheed_id}/sort', payload)

        if response.__class__ != smartsheet.Smartsheet.models.Error:
            time.sleep(5)
            new_row_ids = [row['id'] for row in response.data['result']]
            print('new_row_ids', new_row_ids)
            sheet = smart.Sheets.get_sheet(sheed_id)
            newRows = [row for row in sheet.rows if row.id in new_row_ids]
            for row in newRows:
                if (not row.cells[5].value or ':' not in row.cells[5].value):
                    print(f'sending reminder to add inactive hours for cluster - {row.cells[1].value}')
                    send_request_to_update_inactive_hours(row, column_map, smart)


    if smartsheet_deleted_data:
        print('Deleting old clusters', smartsheet_deleted_data)
        delete_url = f'/sheets/{sheed_id}/rows?ids={",".join(smartsheet_deleted_data)}'
        print(delete_url)
        response = smart.Passthrough.delete(delete_url)
        # print(response)

def send_request_to_update_inactive_hours(row:smartsheet.smartsheet.models.row, column_map:dict, smart:smartsheet.smartsheet.Smartsheet):
    sheed_id = 7086931905040260
    payload  = open('refs/update_request.json').read().replace('__CLUSTER__NAME__', row.cells[1].value)
    payload = json.loads(payload)
    payload['rowIds']= [row.id]
    payload['columnIds'] = [value for key, value in column_map.items()]
    payload['sendTo'] = [{'email': row.cells[7].value}]
    # , {'email': 'ikhalidi@redhat.com'}
    response = smart.Passthrough.post(f'/sheets/{sheed_id}/updaterequests', payload)
    # print(response)




def get_instances_for_region(region, current_state):
    ec2_client = boto3.client('ec2', region_name=region)
    filters = [{'Name': 'instance-state-name', 'Values': [current_state]}]
    ec2_map = ec2_client.describe_instances(Filters=filters, MaxResults=1000)
    ec2_map = [ec2 for ec2 in ec2_map['Reservations']]
    ec2_map = [instance for ec2 in ec2_map for instance in ec2['Instances']]
    ec2_map = {list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))[0]['Value']: instance for instance in
               ec2_map if list(filter(lambda obj: obj['Key'] == 'Name', instance['Tags']))}
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

def update_rosa_hosted_clusters_status(clusters:list[oc_cluster]):
    ec2_instances = {}
    get_all_instances(ec2_instances, 'running')
    for cluster in clusters:
        if cluster.type == 'rosa' and cluster.hcp == 'true':
            worker_instances = [instance_name for instance_name in ec2_instances[cluster.region] if instance_name.startswith(f'{cluster.name}-') and worker_node_belongs_to_the_hcp_cluster(ec2_instances[cluster.region][instance_name], cluster.name)]
            if len(worker_instances) == 0:
                cluster.status = 'hibernating'

def main():
    clusters = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)
    update_rosa_hosted_clusters_status(clusters)

    names = [cluster.name for cluster in clusters]
    # print(names)
    update_smartsheet_data(clusters)




if __name__ == '__main__':
    main()