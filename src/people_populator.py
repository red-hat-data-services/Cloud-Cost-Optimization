import json
import time

import boto3
import os
import smartsheet
import re

class employee:
    def __init__(self, details:str):
        parts = details.strip('\n').split(':')
        self.emp_email = parts[0]
        self.mgr_email = parts[1]

def parse_people_details():
    employees = []
    people_details = open('people_details.txt').readlines()
    for people_detail in people_details:
        employees.append(employee(people_detail))

    return employees


def get_people_details(org:str):
    run_command(f'../script/./get_people_details.sh {org}')

def run_command(command):
    output = os.popen(command).read()
    print(output)
    return output

def build_cells(employee: employee, column_map:dict):
    cells = []

    column_object = {}
    column_object['columnId'] = column_map['ID']
    column_object['value'] = employee.emp_email.split('@')[0]
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Employee']
    column_object['value'] = employee.emp_email
    cells.append(column_object)

    column_object = {}
    column_object['columnId'] = column_map['Manager']
    column_object['value'] = employee.mgr_email
    cells.append(column_object)

    return cells

def update_smartsheet_data(employees:list[employee]):
    column_map = {}
    smart = smartsheet.Smartsheet()
    # response = smart.Sheets.list_sheets()
    sheed_id = 1218217942929284
    sheet = smart.Sheets.get_sheet(sheed_id)
    for column in sheet.columns:
        column_map[column.title] = column.id
    print(column_map)

    # process existing data
    existingRows = {row.cells[1].value: row.id for row in sheet.rows}

    smartsheet_existing_data = []
    smartsheet_new_data = []

    for employee in employees:
        rowObject = {}
        if employee.emp_email in existingRows:
            rowObject['id'] = existingRows[employee.emp_email]
            rowObject['cells'] = build_cells(employee, column_map)
            smartsheet_existing_data.append(rowObject)
        else:
            rowObject['toBottom'] = True
            rowObject['cells'] = build_cells(employee, column_map)
            smartsheet_new_data.append(rowObject)

    if smartsheet_existing_data:
        payload = json.dumps(smartsheet_existing_data, indent=4)
        print('Updating existing employees', payload)
        response = smart.Passthrough.put(f'/sheets/{sheed_id}/rows', payload)
        print(response)

    if smartsheet_new_data:
        payload = json.dumps(smartsheet_new_data, indent=4)
        print('Adding new employees', payload)
        response = smart.Passthrough.post(f'/sheets/{sheed_id}/rows', payload)
        print(response)
        payload = json.dumps({'sortCriteria': [{'columnId': column_map['Employee'], 'direction': 'ASCENDING'}]})
        response_sort = smart.Passthrough.post(f'/sheets/{sheed_id}/sort', payload)




def main():
    print(os.getcwd())
    orgs = ['shgriffi']
    # for org in orgs:
    #     get_people_details(org)

    employees = parse_people_details()
    update_smartsheet_data(employees)
    print([f'{employee.emp_email}:{employee.mgr_email}' for employee in employees])

if __name__ == '__main__':
    main()