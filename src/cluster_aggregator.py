import json
import boto3
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
        self.ocm_account = ocm_account

def get_all_cluster_details(ocm_account:str, clusters:list):
    get_cluster_list(ocm_account)
    clusters_details = open(f'clusters_{ocm_account}.txt').readlines()
    for cluster_detail in clusters_details:
        clusters.append(oc_cluster(cluster_detail, ocm_account))
    clusters = [cluster for cluster in clusters if cluster.cloud_provider == 'aws']

def get_cluster_list(ocm_account:str):
    run_command(f'script/./get_all_cluster_details.sh {ocm_account}')

def run_command(command):
    output = os.popen(command).read()
    print(output)
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
    #
    # column_object = {}
    # column_object['columnId'] = column_map['Owner']
    # column_object['value'] = row_id
    # cells.append(column_object)
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
        print(response)

    if smartsheet_new_data:
        payload = json.dumps(smartsheet_new_data, indent=4)
        print('Adding new clusters', payload)
        response = smart.Passthrough.post(f'/sheets/{sheed_id}/rows', payload)
        print(response)
    payload = json.dumps({'sortCriteria': [{'columnId': column_map['Name'], 'direction': 'ASCENDING'}]})
    response = smart.Passthrough.post(f'/sheets/{sheed_id}/sort', payload)

    if smartsheet_deleted_data:
        print('Deleting old clusters', smartsheet_deleted_data)
        delete_url = f'/sheets/{sheed_id}/rows?ids={",".join(smartsheet_deleted_data)}'
        print(delete_url)
        response = smart.Passthrough.delete(delete_url)
        print(response)


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
def update_rosa_hosted_clusters_status(clusters:list[oc_cluster]):
    ec2_instances = {}
    get_all_instances(ec2_instances, 'running')
    for cluster in clusters:
        if cluster.type == 'rosa' and cluster.hcp == 'true':
            worker_instances = [instance_name for instance_name in ec2_instances[cluster.region] if instance_name.startswith(f'{cluster.name}-workers-')]
            if len(worker_instances) == 0:
                cluster.status = 'hibernating'

def main():
    clusters = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)
    update_rosa_hosted_clusters_status(clusters)

    names = [cluster.name for cluster in clusters]
    print(names)
    update_smartsheet_data(clusters)




if __name__ == '__main__':
    main()