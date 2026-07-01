import json

import boto3
import utils
from resume_cluster import resume_generic_cluster


def get_last_hibernated():
    s3 = boto3.client('s3')
    s3.download_file('rhods-devops', 'Cloud-Cost-Optimization/Weekend-Hibernation/hibernated_latest.json', 'hibernated_latest.json')


def main():
    get_last_hibernated()

    print("=== Identifying clusters to resume ===", flush=True)
    clusters_to_resume = []
    clusters = json.load(open('hibernated_latest.json'))
    for cluster in clusters:
        clusters_to_resume.append(utils.OcCluster(cluster))

    print("=== Resuming clusters ===", flush=True)
    resumed_clusters = []
    for cluster in clusters_to_resume:
        resume_generic_cluster(cluster)
        resumed_clusters.append(cluster.__dict__)


if __name__ == '__main__':
    main()
