import json
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
    print(output)
    return output


def send_weekly_reminder(clusters:dict[oc_cluster]):
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 7086931905040260
    sheet = smart.Sheets.get_sheet(sheed_id)
    for column in sheet.columns:
        column_map[column.title] = column.id
    print(column_map)

    # process existing data
    existingClusterIds = [cluster.id for cluster in clusters]

    for row in sheet.rows:
        if row.cells[0].value in existingClusterIds and (not row.cells[5].value or ':' not in row.cells[5].value) and row.cells[1].value == 'dchouras':
            print(f'sending weekly reminder for cluster - {row.cells[1].value}')
            send_request_to_update_inactive_hours(row, column_map, smart)


def send_request_to_update_inactive_hours(row:smartsheet.smartsheet.models.row, column_map:dict, smart:smartsheet.smartsheet.Smartsheet):
    sheed_id = 7086931905040260
    payload  = open('refs/update_request.json').read().replace('__CLUSTER__NAME__', row.cells[1].value)
    payload = json.loads(payload)
    payload['rowIds']= [row.id]
    payload['columnIds'] = [value for key, value in column_map.items()]
    payload['sendTo'] = [{'email': row.cells[7].value}]
    # , {'email': 'ikhalidi@redhat.com'}
    response = smart.Passthrough.post(f'/sheets/{sheed_id}/updaterequests', payload)
    print(response)

def main():
    clusters = []
    ocm_accounts = ['PROD', 'STAGE']

    for ocm_account in ocm_accounts:
        get_all_cluster_details(ocm_account, clusters)
    send_weekly_reminder(clusters)




if __name__ == '__main__':
    main()