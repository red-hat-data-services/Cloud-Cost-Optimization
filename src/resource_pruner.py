"""
AWS Resource Pruner

Reads JSON output from resource_tracer.py and terminates the listed EC2 instances.

Usage:
    # Dry run (default) — shows what would be terminated
    python src/resource_tracer.py --scan --region us-east-1 --filter prunable --output json | \
        python src/resource_pruner.py --region us-east-1 -

    # Actual termination
    python src/resource_pruner.py --region us-east-1 --dry-run false prunable.json
"""

import argparse
import json
import sys
from collections import defaultdict
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


BATCH_SIZE = 1000


def load_traces(input_file):
    if input_file == "-":
        data = sys.stdin.read()
    else:
        with open(input_file) as f:
            data = f.read()

    traces = json.loads(data)
    if not isinstance(traces, list):
        print("Error: expected a JSON array")
        sys.exit(1)

    for t in traces:
        if "resource_id" not in t or "resource_type" not in t:
            print(f"Error: entry missing resource_id or resource_type: {t}")
            sys.exit(1)

    return traces


def terminate_instances(ec2, traces, dry_run):
    ec2_traces = [t for t in traces if t["resource_type"] == "ec2"]
    skipped_type = len(traces) - len(ec2_traces)
    if skipped_type:
        print(f"Skipping {skipped_type} non-EC2 resources")

    skip_states = {"terminated", "terminating"}
    actionable = [t for t in ec2_traces if t.get("state", "") not in skip_states]
    skipped_state = len(ec2_traces) - len(actionable)
    if skipped_state:
        print(f"Skipping {skipped_state} already terminated/terminating instances")

    if not actionable:
        print("No instances to terminate.")
        return

    by_cluster = defaultdict(list)
    for t in actionable:
        key = t.get("cluster_name") or "Unassociated"
        by_cluster[key].append(t)

    if dry_run:
        print(f"\n=== DRY RUN — {len(actionable)} instances would be terminated ===\n")
        for cluster, instances in sorted(by_cluster.items()):
            cluster_type = instances[0].get("cluster_type", "unknown")
            prunability = instances[0].get("prunability", "")
            label = f"{cluster} ({cluster_type})"
            if prunability:
                label += f" [{prunability.upper()}]"
            print(f"  {label}")
            for t in instances:
                print(f"    {t['resource_id']}  {t.get('instance_type', '')}  {t.get('state', '')}")
            print()
        print(f"Total: {len(actionable)} instances across {len(by_cluster)} clusters")
        print("Run with --dry-run false to terminate.")
        return

    print(f"\n=== TERMINATING {len(actionable)} instances ===\n")
    instance_ids = [t["resource_id"] for t in actionable]
    success = 0
    failed = []

    for i in range(0, len(instance_ids), BATCH_SIZE):
        batch = instance_ids[i : i + BATCH_SIZE]
        print(f"Terminating batch of {len(batch)} instances...")
        for iid in batch:
            print(f"  {iid}")
        try:
            ec2.terminate_instances(InstanceIds=batch)
            success += len(batch)
        except ClientError as e:
            print(f"  Error terminating batch: {e}")
            failed.extend(batch)

    if success:
        print(f"\nWaiting for {success} instances to reach terminated state...")
        try:
            waiter = ec2.get_waiter("instance_terminated")
            waiter.wait(
                InstanceIds=[iid for iid in instance_ids if iid not in failed],
                WaiterConfig={"Delay": 15, "MaxAttempts": 40},
            )
            print("All instances terminated.")
        except Exception as e:
            print(f"Warning: waiter error (instances may still be terminating): {e}")

    print(f"\nResult: {success} terminated, {len(failed)} failed")
    if failed:
        print("Failed instance IDs:")
        for iid in failed:
            print(f"  {iid}")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Resource Pruner — terminates EC2 instances from resource_tracer.py JSON output"
    )
    parser.add_argument(
        "input_file",
        help='Path to JSON file from resource_tracer.py, or "-" for stdin',
    )
    parser.add_argument(
        "--dry-run",
        type=str,
        choices=["true", "false"],
        default="true",
        help="Dry-run mode: true or false (default: true)",
    )
    parser.add_argument(
        "--region",
        default="us-west-2",
        help="AWS region (default: us-west-2)",
    )

    args = parser.parse_args()
    dry_run = args.dry_run == "true"

    print("AWS Resource Pruner")
    print("=" * 40)
    if dry_run:
        print("Mode: DRY RUN")
    else:
        print("Mode: LIVE — instances will be terminated")
    print(f"Region: {args.region}\n")

    try:
        ec2 = boto3.Session(region_name=args.region).client("ec2")
    except NoCredentialsError:
        print("Error: AWS credentials not found.")
        sys.exit(1)

    traces = load_traces(args.input_file)
    print(f"Loaded {len(traces)} resources from input")
    terminate_instances(ec2, traces, dry_run)


if __name__ == "__main__":
    main()
