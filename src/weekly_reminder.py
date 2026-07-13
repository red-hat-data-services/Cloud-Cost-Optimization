import json
import smartsheet
import utils


def send_weekly_reminder(clusters:dict[utils.OcCluster]):
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    sheet = smart.Sheets.get_sheet(sheed_id)
    for column in sheet.columns:
        column_map[column.title] = column.id

    # process existing data
    existingClusterIds = [cluster.id for cluster in clusters]

    for row in sheet.rows:
        if row.cells[0].value in existingClusterIds and (not row.cells[5].value or ':' not in row.cells[5].value):
            print(f'sending weekly reminder for cluster - {row.cells[1].value}')
            send_request_to_update_inactive_hours(row, column_map, smart)


def send_request_to_update_inactive_hours(row:smartsheet.smartsheet.models.row, column_map:dict, smart:smartsheet.smartsheet.Smartsheet):
    sheed_id = 7086931905040260
    payload  = open('refs/update_request.json').read().replace('__CLUSTER__NAME__', row.cells[1].value)
    payload = json.loads(payload)
    payload['rowIds']= [row.id]
    payload['columnIds'] = [value for key, value in column_map.items()]
    payload['sendTo'] = [{'email': row.cells[7].value}, {'email': 'ldimaggi@redhat.com'}, {'email': 'mhorinek@redhat.com'}]
    # , {'email': 'ikhalidi@redhat.com'}
    response = smart.Passthrough.post(f'/sheets/{sheed_id}/updaterequests', payload)
    # print(response)


def main():
    clusters = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        utils.get_all_cluster_details(ocm_account, clusters)
    send_weekly_reminder(clusters)

if __name__ == '__main__':
    main()
