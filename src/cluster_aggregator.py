import json
import time
import smartsheet

import utils
from utils import InstanceState


def build_cells(cluster: utils.OcCluster, column_map:dict):
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

def update_smartsheet_data(clusters:dict[utils.OcCluster], allow_deletion=True):
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    sheet = smart.Sheets.get_sheet(sheed_id)
    for column in sheet.columns:
        column_map[column.title] = column.id

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
        print('Updating existing clusters')
        response = smart.Passthrough.put(f'/sheets/{sheed_id}/rows', payload)
        # print(response)

    if smartsheet_new_data:
        payload = json.dumps(smartsheet_new_data, indent=4)
        print('Adding new clusters...')
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


    if smartsheet_deleted_data and allow_deletion:
        print('Deleting old clusters...')
        delete_url = f'/sheets/{sheed_id}/rows?ids={",".join(smartsheet_deleted_data)}'
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


def update_rosa_hosted_clusters_status(clusters:list[utils.OcCluster]):
    ec2_instances = {}
    utils.get_all_instances(ec2_instances, InstanceState.running)
    for cluster in clusters:
        if cluster.type == 'rosa' and cluster.hcp == 'true':
            worker_instances = [instance_name for instance_name in ec2_instances[cluster.region] if utils.worker_node_belongs_to_the_hcp_cluster(ec2_instances[cluster.region][instance_name], cluster.name)]
            if len(worker_instances) == 0:
                cluster.status = 'hibernating'


def main(cluster_list=None, needs_data_refresh=True, allow_smartsheet_deletion=True):
    if cluster_list is None:
        clusters = []
    else:
        clusters = cluster_list
    ocm_accounts = ['PROD', 'STAGE']

    if needs_data_refresh:
        for ocm_account in ocm_accounts:
            utils.get_all_cluster_details(ocm_account, clusters)
        update_rosa_hosted_clusters_status(clusters)
    else:
        utils.update_cluster_details(clusters)

    names = [cluster.name for cluster in clusters]
    # print(names)
    update_smartsheet_data(clusters, allow_deletion=allow_smartsheet_deletion)




if __name__ == '__main__':
    main()