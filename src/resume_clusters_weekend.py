import json
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_cluster = {executor.submit(resume_generic_cluster, c): c for c in clusters_to_resume}
        for future in as_completed(future_to_cluster):
            cluster = future_to_cluster[future]
            try:
                future.result()
                resumed_clusters.append(cluster.__dict__)
            except Exception as e:
                print(f"ERROR: Failed to resume cluster {cluster.name}: {e}", flush=True)


if __name__ == '__main__':
    main()
