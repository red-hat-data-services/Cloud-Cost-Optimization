"""
AWS Resource Pruner

Reads JSON output from resource_tracer.py and deletes the listed resources.
Supports EC2 instances, NAT gateways, and VPC endpoints.

Usage:
    # Dry run (default) — shows what would be deleted
    python src/resource_tracer.py --scan --region us-east-1 --filter prunable --output json | \
        python src/resource_pruner.py --region us-east-1 -

    # Actual deletion
    python src/resource_pruner.py --region us-east-1 --dry-run false prunable.json
"""

import argparse
import json
import sys
from collections import defaultdict
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


EC2_BATCH_SIZE = 1000
VPCE_BATCH_SIZE = 25


def load_traces(input_file, include_questionable=False):
    if input_file == "-":
        data = sys.stdin.read()
    else:
        with open(input_file) as f:
            data = f.read()

    traces = json.loads(data)
    if not isinstance(traces, list):
        print("Error: expected a JSON array")
        sys.exit(1)

    allowed_prunability = {"prunable"}
    if include_questionable:
        allowed_prunability.add("questionable")

    required_fields = {"resource_id", "resource_type", "state", "prunability"}
    for t in traces:
        missing = required_fields - t.keys()
        if missing:
            print(f"Error: entry missing {', '.join(sorted(missing))}: {t.get('resource_id', '<unknown>')}")
            sys.exit(1)

    rejected = [t for t in traces if t.get("prunability") not in allowed_prunability]
    if rejected:
        label = "prunable or questionable" if include_questionable else "prunable"
        print(f"Refusing to delete {len(rejected)} resources not marked {label}:")
        for t in rejected:
            print(f"  {t['resource_id']} (prunability={t.get('prunability', '<missing>')})")
        sys.exit(1)

    return traces


def _print_dry_run(actionable, resource_label):
    by_cluster = defaultdict(list)
    for t in actionable:
        key = t.get("cluster_name") or "Unassociated"
        by_cluster[key].append(t)

    print(f"\n=== DRY RUN — {len(actionable)} {resource_label} would be deleted ===\n")
    for cluster, resources in sorted(by_cluster.items()):
        cluster_type = resources[0].get("cluster_type", "unknown")
        prunability = resources[0].get("prunability", "")
        label = f"{cluster} ({cluster_type})"
        if prunability:
            label += f" [{prunability.upper()}]"
        print(f"  {label}")
        for t in resources:
            extra = f"  {t.get('instance_type', '')}" if t.get("instance_type") else ""
            print(f"    {t['resource_id']}{extra}  {t.get('state', '')}")
        print()
    print(f"Total: {len(actionable)} {resource_label} across {len(by_cluster)} clusters")
    print("Run with --dry-run false to delete.")


def terminate_instances(ec2, traces, dry_run):
    skip_states = {"terminated", "terminating"}
    actionable = [t for t in traces if t.get("state", "") not in skip_states]
    skipped = len(traces) - len(actionable)
    if skipped:
        print(f"Skipping {skipped} already terminated/terminating instances")

    if not actionable:
        print("No EC2 instances to terminate.")
        return

    if dry_run:
        _print_dry_run(actionable, "instances")
        return

    print(f"\n=== TERMINATING {len(actionable)} instances ===\n")
    instance_ids = list(dict.fromkeys(t["resource_id"] for t in actionable))
    success = 0
    failed = []

    for i in range(0, len(instance_ids), EC2_BATCH_SIZE):
        batch = instance_ids[i : i + EC2_BATCH_SIZE]
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


def delete_nat_gateways(ec2, traces, dry_run):
    skip_states = {"deleted", "deleting"}
    actionable = [t for t in traces if t.get("state", "") not in skip_states]
    skipped = len(traces) - len(actionable)
    if skipped:
        print(f"Skipping {skipped} already deleted/deleting NAT gateways")

    if not actionable:
        print("No NAT gateways to delete.")
        return

    if dry_run:
        _print_dry_run(actionable, "NAT gateways")
        return

    print(f"\n=== DELETING {len(actionable)} NAT gateways ===\n")
    success = 0
    failed = []

    for t in actionable:
        nat_id = t["resource_id"]
        print(f"  Deleting {nat_id}...")
        try:
            ec2.delete_nat_gateway(NatGatewayId=nat_id)
            success += 1
        except ClientError as e:
            print(f"    Error: {e}")
            failed.append(nat_id)

    print(f"\nResult: {success} deleted, {len(failed)} failed")
    if failed:
        print("Failed NAT gateway IDs:")
        for nid in failed:
            print(f"  {nid}")


def delete_vpc_endpoints(ec2, traces, dry_run):
    skip_states = {"deleted", "deleting"}
    actionable = [t for t in traces if t.get("state", "") not in skip_states]
    skipped = len(traces) - len(actionable)
    if skipped:
        print(f"Skipping {skipped} already deleted/deleting VPC endpoints")

    if not actionable:
        print("No VPC endpoints to delete.")
        return

    if dry_run:
        _print_dry_run(actionable, "VPC endpoints")
        return

    print(f"\n=== DELETING {len(actionable)} VPC endpoints ===\n")
    endpoint_ids = list(dict.fromkeys(t["resource_id"] for t in actionable))
    success = 0
    failed = []

    for i in range(0, len(endpoint_ids), VPCE_BATCH_SIZE):
        batch = endpoint_ids[i : i + VPCE_BATCH_SIZE]
        print(f"Deleting batch of {len(batch)} VPC endpoints...")
        for eid in batch:
            print(f"  {eid}")
        try:
            resp = ec2.delete_vpc_endpoints(VpcEndpointIds=batch)
            unsuccessful = resp.get("Unsuccessful", [])
            batch_failed = [item["ResourceId"] for item in unsuccessful]
            failed.extend(batch_failed)
            success += len(batch) - len(batch_failed)
        except ClientError as e:
            print(f"  Error deleting batch: {e}")
            failed.extend(batch)

    print(f"\nResult: {success} deleted, {len(failed)} failed")
    if failed:
        print("Failed VPC endpoint IDs:")
        for eid in failed:
            print(f"  {eid}")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Resource Pruner — deletes resources from resource_tracer.py JSON output"
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
    parser.add_argument(
        "--include-questionable", action="store_true",
        help="Also accept resources marked 'questionable' (default: only 'prunable')",
    )

    args = parser.parse_args()
    dry_run = args.dry_run == "true"

    print("AWS Resource Pruner")
    print("=" * 40)
    if dry_run:
        print("Mode: DRY RUN")
    else:
        print("Mode: LIVE — resources will be deleted")
    if args.include_questionable:
        print("Accepting: prunable + questionable")
    print(f"Region: {args.region}\n")

    try:
        ec2 = boto3.Session(region_name=args.region).client("ec2")
    except NoCredentialsError:
        print("Error: AWS credentials not found.")
        sys.exit(1)

    traces = load_traces(args.input_file, args.include_questionable)
    print(f"Loaded {len(traces)} resources from input")

    ec2_traces = [t for t in traces if t["resource_type"] == "ec2"]
    nat_traces = [t for t in traces if t["resource_type"] == "nat-gateway"]
    vpce_traces = [t for t in traces if t["resource_type"] == "vpc-endpoint"]
    other = len(traces) - len(ec2_traces) - len(nat_traces) - len(vpce_traces)
    if other:
        print(f"Skipping {other} unsupported resource types")

    if ec2_traces:
        terminate_instances(ec2, ec2_traces, dry_run)
    if nat_traces:
        delete_nat_gateways(ec2, nat_traces, dry_run)
    if vpce_traces:
        delete_vpc_endpoints(ec2, vpce_traces, dry_run)


if __name__ == "__main__":
    main()
