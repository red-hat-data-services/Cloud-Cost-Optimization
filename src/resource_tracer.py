"""
AWS Resource Tracer

Read-only inspection tool that traces AWS resources back to their OpenShift
clusters and identifies who provisioned them. Supports EC2 instances,
NAT gateways, VPC endpoints, and Elastic IPs.

Usage:
  python src/resource_tracer.py i-01d154018d489351c --region us-east-1
  python src/resource_tracer.py --scan --region us-east-1
  python src/resource_tracer.py --scan --region us-east-1 --resource-type nat-gateway
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


RESOURCE_TYPE_LABELS = {
    "ec2": "EC2 Instances",
    "nat-gateway": "NAT Gateways",
    "vpc-endpoint": "VPC Endpoints",
    "eip": "Elastic IPs",
}

DEFAULT_STATES = {
    "ec2": "running,stopped",
    "nat-gateway": "available,failed",
    "vpc-endpoint": "available,pending",
    "eip": "all",
}


@dataclass
class ResourceTrace:
    resource_id: str
    resource_type: str
    instance_type: str = ""
    state: str = ""
    name: str = ""
    launch_time: str = ""
    age_str: str = ""
    cluster_name: str = ""
    cluster_type: str = "unknown"
    cluster_id: str = ""
    owner_name: str = ""
    owner_email: str = ""
    ocm_account: str = ""
    expiration_date: str = ""
    is_expired: bool = False
    expired_ago: str = ""
    prow_job: str = ""
    prow_build_id: str = ""
    prunability: str = ""
    tags: dict = field(default_factory=dict)


# ── Fetching ─────────────────────────────────────────────────

def fetch_ec2_instances(ec2_client, instance_ids=None, states=None):
    instances = []
    paginator = ec2_client.get_paginator("describe_instances")

    try:
        if instance_ids:
            pages = paginator.paginate(InstanceIds=instance_ids)
        elif states:
            pages = paginator.paginate(
                Filters=[{"Name": "instance-state-name", "Values": states}]
            )
        else:
            pages = paginator.paginate()

        for page in pages:
            for reservation in page["Reservations"]:
                instances.extend(reservation["Instances"])

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "InvalidInstanceID.NotFound":
            print(f"Warning: {e.response['Error']['Message']}")
        else:
            print(f"Error fetching instances: {e}")

    return instances


def fetch_nat_gateways(ec2_client, nat_gateway_ids=None, states=None):
    gateways = []
    paginator = ec2_client.get_paginator("describe_nat_gateways")

    try:
        if nat_gateway_ids:
            pages = paginator.paginate(NatGatewayIds=nat_gateway_ids)
        elif states:
            pages = paginator.paginate(
                Filters=[{"Name": "state", "Values": states}]
            )
        else:
            pages = paginator.paginate()

        for page in pages:
            gateways.extend(page["NatGateways"])

    except ClientError as e:
        print(f"Error fetching NAT gateways: {e}")

    return gateways


def fetch_vpc_endpoints(ec2_client, endpoint_ids=None, states=None):
    endpoints = []
    paginator = ec2_client.get_paginator("describe_vpc_endpoints")

    try:
        if endpoint_ids:
            pages = paginator.paginate(VpcEndpointIds=endpoint_ids)
        elif states:
            pages = paginator.paginate(
                Filters=[{"Name": "vpc-endpoint-state", "Values": states}]
            )
        else:
            pages = paginator.paginate()

        for page in pages:
            endpoints.extend(page["VpcEndpoints"])

    except ClientError as e:
        print(f"Error fetching VPC endpoints: {e}")

    return endpoints


def fetch_eips(ec2_client, allocation_ids=None, states=None):
    try:
        if allocation_ids:
            response = ec2_client.describe_addresses(AllocationIds=allocation_ids)
        else:
            response = ec2_client.describe_addresses(
                Filters=[{"Name": "domain", "Values": ["vpc"]}]
            )
        return response.get("Addresses", [])
    except ClientError as e:
        print(f"Error fetching Elastic IPs: {e}")
        return []


# ── Tag helpers ───────────────────────────────────────────────

def _tags_to_dict(resource):
    return {t["Key"]: t["Value"] for t in resource.get("Tags", [])}


def _get_kubernetes_cluster_name(tags):
    for key, value in tags.items():
        if key.startswith("kubernetes.io/cluster/") and value == "owned":
            return key.split("kubernetes.io/cluster/")[1]
    return None


def _parse_expiration(tags):
    raw = tags.get("expirationDate")
    if not raw:
        return None, False
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return dt, now > dt
    except (ValueError, TypeError):
        return None, False


def _format_age(dt):
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days}d {hours}h"
    return f"{hours}h"


def _age_days(launch_time_str, now):
    if not launch_time_str:
        return 0
    try:
        dt = datetime.fromisoformat(launch_time_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days
    except (ValueError, TypeError):
        return 0


# ── Classification ────────────────────────────────────────────

def _classify_by_tags(tags, trace):
    k8s_cluster = _get_kubernetes_cluster_name(tags)
    exp_dt, is_expired = _parse_expiration(tags)

    if exp_dt:
        trace.expiration_date = exp_dt.isoformat()
        trace.is_expired = is_expired
        if is_expired:
            trace.expired_ago = _format_age(exp_dt)

    # 1. Prow CI
    if tags.get("prow.k8s.io/build-id") and tags.get("prow.k8s.io/job"):
        trace.cluster_type = "prow-ci"
        trace.prow_job = tags["prow.k8s.io/job"]
        trace.prow_build_id = tags["prow.k8s.io/build-id"]
        trace.cluster_name = k8s_cluster or ""
        return trace

    # 2. ROSA HCP — has api.openshift.com/name
    if "api.openshift.com/name" in tags:
        trace.cluster_name = tags["api.openshift.com/name"]
        trace.cluster_id = tags.get("api.openshift.com/id", "")
        clustertype = tags.get("red-hat-clustertype", "")
        if clustertype == "osd":
            trace.cluster_type = "osd-hcp"
        else:
            trace.cluster_type = "rosa-hcp"
        return trace

    # 3. ROSA Classic / OSD — has red-hat-clustertype but no api.openshift.com/name
    if "red-hat-clustertype" in tags:
        clustertype = tags["red-hat-clustertype"]
        trace.cluster_type = "rosa-classic" if clustertype == "rosa" else "osd"
        trace.cluster_id = tags.get("api.openshift.com/id", "")
        trace.cluster_name = k8s_cluster or ""
        return trace

    # 4. IPI — has kubernetes.io/cluster/* with owned
    if k8s_cluster:
        trace.cluster_type = "ipi"
        trace.cluster_name = k8s_cluster
        return trace

    # 5. Unknown
    trace.cluster_type = "unknown"
    return trace


def classify_ec2_instance(instance):
    tags = _tags_to_dict(instance)
    launch_time = instance.get("LaunchTime")

    trace = ResourceTrace(
        resource_id=instance["InstanceId"],
        resource_type="ec2",
        instance_type=instance.get("InstanceType", ""),
        state=instance.get("State", {}).get("Name", ""),
        name=tags.get("Name", ""),
        launch_time=launch_time.isoformat() if launch_time else "",
        age_str=_format_age(launch_time),
        tags=tags,
    )
    return _classify_by_tags(tags, trace)


def classify_nat_gateway(nat_gw):
    tags = _tags_to_dict(nat_gw)
    create_time = nat_gw.get("CreateTime")

    trace = ResourceTrace(
        resource_id=nat_gw["NatGatewayId"],
        resource_type="nat-gateway",
        state=nat_gw.get("State", ""),
        name=tags.get("Name", ""),
        launch_time=create_time.isoformat() if create_time else "",
        age_str=_format_age(create_time),
        tags=tags,
    )
    return _classify_by_tags(tags, trace)


def classify_vpc_endpoint(endpoint):
    tags = _tags_to_dict(endpoint)
    create_time = endpoint.get("CreationTimestamp")

    trace = ResourceTrace(
        resource_id=endpoint["VpcEndpointId"],
        resource_type="vpc-endpoint",
        state=endpoint.get("State", ""),
        name=tags.get("Name", ""),
        launch_time=create_time.isoformat() if create_time else "",
        age_str=_format_age(create_time),
        tags=tags,
    )
    return _classify_by_tags(tags, trace)


def classify_eip(address):
    tags = _tags_to_dict(address)
    state = "associated" if address.get("AssociationId") else "unassociated"

    trace = ResourceTrace(
        resource_id=address["AllocationId"],
        resource_type="eip",
        instance_type=address.get("PublicIp", ""),
        state=state,
        name=tags.get("Name", ""),
        tags=tags,
    )
    return _classify_by_tags(tags, trace)


# ── OCM integration ──────────────────────────────────────────

def _ocm_login(account):
    token = os.environ.get("OCM_TOKEN", "")
    if not token:
        result = subprocess.run(
            ["ocm", "whoami"], capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True
        print(f"Warning: OCM_TOKEN not set and no active session for {account}")
        return False

    cmd = ["ocm", "login", "--token", token]
    if account == "STAGE":
        cmd.extend(["--url", "stage"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Warning: OCM login failed for {account}: {result.stderr.strip()}")
        return False
    return True


def _ocm_list_clusters(account):
    clusters = {}
    page = 1
    page_size = 100

    while True:
        result = subprocess.run(
            ["ocm", "get", "/api/clusters_mgmt/v1/clusters",
             "--parameter", f"page={page}",
             "--parameter", f"size={page_size}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"Warning: Could not list OCM clusters ({account}): {result.stderr.strip()}")
            return clusters

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            print(f"Warning: Invalid JSON from OCM cluster list ({account})")
            return clusters

        for item in data.get("items", []):
            cluster_id = item.get("id", "")
            name = item.get("name", "")
            entry = {
                "id": cluster_id,
                "name": name,
                "type": item.get("product", {}).get("id", ""),
                "hcp": str(item.get("hypershift", {}).get("enabled", False)).lower(),
                "region": item.get("region", {}).get("id", ""),
                "status": item.get("state", ""),
                "ocm_account": account,
            }
            clusters[name] = entry
            clusters[cluster_id] = entry

        total = data.get("total", 0)
        if page * page_size >= total:
            break
        page += 1

    return clusters


def load_ocm_clusters(ocm_accounts):
    ocm_clusters = {}
    for account in ocm_accounts:
        if _ocm_login(account):
            clusters = _ocm_list_clusters(account)
            ocm_clusters.update(clusters)
    return ocm_clusters


def lookup_cluster_owner(cluster_id, owner_cache):
    if cluster_id in owner_cache:
        return owner_cache[cluster_id]

    result = subprocess.run(
        ["ocm", "get", "/api/accounts_mgmt/v1/subscriptions",
         "-p", f"search=cluster_id='{cluster_id}'",
         "--parameter", "fetchAccounts=true"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        owner_cache[cluster_id] = ("", "")
        return ("", "")

    try:
        data = json.loads(result.stdout)
        items = data.get("items", [])
        if not items:
            owner_cache[cluster_id] = ("", "")
            return ("", "")
        creator = items[0].get("creator", {})
        name = creator.get("name", "") or \
            f"{creator.get('first_name', '')} {creator.get('last_name', '')}".strip()
        email = _get_original_email_address(creator.get("email", ""))
        owner_cache[cluster_id] = (name, email)
        return (name, email)
    except (json.JSONDecodeError, KeyError, IndexError):
        owner_cache[cluster_id] = ("", "")
        return ("", "")


def enrich_with_ocm(traces, ocm_clusters, owner_cache):
    by_account = defaultdict(dict)
    for trace in traces:
        if trace.cluster_type in ("unknown", "prow-ci"):
            continue
        ocm_entry = (
            ocm_clusters.get(trace.cluster_name)
            or ocm_clusters.get(trace.cluster_id)
        )
        if ocm_entry:
            trace.ocm_account = ocm_entry["ocm_account"]
            cid = ocm_entry["id"]
            trace.cluster_id = cid
            account = ocm_entry["ocm_account"]
            if cid not in by_account[account]:
                by_account[account][cid] = []
            by_account[account][cid].append(trace)

    for account, cluster_map in by_account.items():
        if not _ocm_login(account):
            continue
        for cluster_id, group_traces in cluster_map.items():
            owner_name, owner_email = lookup_cluster_owner(cluster_id, owner_cache)
            for t in group_traces:
                t.owner_name = owner_name
                t.owner_email = owner_email


# ── Prunability ────────────────────────────────────────────────

def classify_prunability(traces, ocm_clusters, age_threshold=30):
    now = datetime.now(timezone.utc)
    managed_types = {"rosa-hcp", "rosa-classic", "osd", "osd-hcp", "ipi"}

    for trace in traces:
        age = _age_days(trace.launch_time, now)

        if trace.cluster_type == "prow-ci":
            if trace.is_expired:
                trace.prunability = "prunable"
            elif not trace.expiration_date and age >= age_threshold:
                trace.prunability = "questionable"
            else:
                trace.prunability = "not-prunable"

        elif trace.cluster_type in managed_types:
            ocm_entry = (
                ocm_clusters.get(trace.cluster_name)
                or ocm_clusters.get(trace.cluster_id)
            )
            ocm_status = ocm_entry.get("status", "") if ocm_entry else ""
            if ocm_status == "error":
                trace.prunability = "questionable"
            elif age >= age_threshold:
                trace.prunability = "questionable"
            else:
                trace.prunability = "not-prunable"

        elif trace.cluster_type == "unknown":
            if age >= age_threshold:
                trace.prunability = "questionable"
            else:
                trace.prunability = "not-prunable"

        else:
            trace.prunability = "not-prunable"


def filter_traces(traces, filter_value):
    if not filter_value:
        return traces
    allowed = set(filter_value.split("+"))
    return [t for t in traces if t.prunability in allowed]


# ── Grouping ──────────────────────────────────────────────────

def group_by_cluster(traces):
    groups = defaultdict(list)
    for t in traces:
        key = (t.cluster_name or t.resource_id, t.cluster_type)
        groups[key].append(t)

    type_order = {
        "rosa-hcp": 0, "rosa-classic": 1, "osd": 2, "osd-hcp": 3,
        "ipi": 4, "prow-ci": 5, "unknown": 6,
    }
    return sorted(groups.items(), key=lambda x: (type_order.get(x[0][1], 99), x[0][0]))


# ── Text report ───────────────────────────────────────────────

def format_text_report(traces, region, resource_types=None, all_traces=None):
    if all_traces is None:
        all_traces = traces
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if resource_types is None:
        resource_types = list(RESOURCE_TYPE_LABELS.keys())
    if isinstance(resource_types, str):
        resource_types = [resource_types]
    rt_label = ", ".join(RESOURCE_TYPE_LABELS.get(rt, rt) for rt in resource_types)
    lines = []
    lines.append(f"AWS Resource Tracer — {region} ({rt_label})")
    lines.append("=" * 60)
    scanned_label = f"Scanned: {len(all_traces)} resources"
    if len(traces) != len(all_traces):
        scanned_label += f" | Showing: {len(traces)}"
    scanned_label += f" | {now_str}"
    lines.append(scanned_label)
    lines.append("")

    grouped = group_by_cluster(traces)

    for (cluster_name, cluster_type), group in grouped:
        sample = group[0]

        if cluster_type == "unknown":
            label = "Unassociated"
        else:
            label = f"{cluster_name} ({_type_label(cluster_type)})"

        prunability_tag = _prunability_tag(group)
        if prunability_tag:
            label += f" {prunability_tag}"

        lines.append(f"── {label} " + "─" * max(1, 58 - len(label)))

        if sample.owner_name or sample.owner_email:
            owner = sample.owner_name
            if sample.owner_email:
                owner += f" <{sample.owner_email}>"
            ocm = f" | OCM: {sample.ocm_account}" if sample.ocm_account else ""
            lines.append(f"   Owner: {owner}{ocm}")

        if cluster_type == "prow-ci" and sample.prow_job:
            lines.append(f"   Job: {sample.prow_job}")
            lines.append(f"   Build: {sample.prow_build_id}")

        if sample.expiration_date:
            exp_status = f"expired {sample.expired_ago} ago" if sample.is_expired else "active"
            lines.append(f"   Expiration: {sample.expiration_date} ({exp_status})")

        if sample.cluster_id:
            lines.append(f"   Cluster ID: {sample.cluster_id}")

        lines.append("")
        lines.append(
            f"   {'RESOURCE':<24} {'TYPE':<14} {'STATE':<10} {'CREATED':<13} {'AGE':<8} {'NAME'}"
        )
        for t in sorted(group, key=lambda x: x.resource_id):
            created = t.launch_time[:10] if t.launch_time else ""
            name_col = t.name if cluster_type == "unknown" else ""
            lines.append(
                f"   {t.resource_id:<24} {t.instance_type:<14} {t.state:<10} {created:<13} {t.age_str:<8} {name_col}"
            )
        lines.append("")

    lines.append(_format_summary(traces, grouped, all_traces))
    return "\n".join(lines)


def format_json_report(traces):
    return json.dumps([asdict(t) for t in traces], indent=2, default=str)


# ── Utilities ─────────────────────────────────────────────────────

def _get_original_email_address(email):
    if not email or "+" not in email.split("@")[0]:
        return email
    local, domain = email.split("@")
    original_local = local.split("+")[0]
    return f"{original_local}@{domain}"


def _prunability_tag(group):
    prunabilities = {t.prunability for t in group if t.prunability}
    if not prunabilities:
        return ""
    if "prunable" in prunabilities:
        return "[PRUNABLE]"
    if "questionable" in prunabilities:
        return "[QUESTIONABLE]"
    return ""


def _type_label(cluster_type):
    labels = {
        "rosa-hcp": "ROSA HCP",
        "rosa-classic": "ROSA Classic",
        "osd": "OSD",
        "osd-hcp": "OSD HCP",
        "ipi": "IPI",
        "prow-ci": "Prow CI",
        "unknown": "Unknown",
    }
    return labels.get(cluster_type, cluster_type)


def _format_summary(traces, grouped, all_traces=None):
    if all_traces is None:
        all_traces = traces
    lines = ["=" * 60, "Summary", "=" * 60]

    type_counts = defaultdict(lambda: {"resources": 0, "clusters": set(), "expired_clusters": set()})
    for (cluster_name, cluster_type), group in grouped:
        tc = type_counts[cluster_type]
        tc["resources"] += len(group)
        tc["clusters"].add(cluster_name)
        if any(t.is_expired for t in group):
            tc["expired_clusters"].add(cluster_name)

    lines.append(f"Total: {len(traces)} resources, {sum(len(tc['clusters']) for tc in type_counts.values())} clusters")
    lines.append("")

    for ct in ["rosa-hcp", "rosa-classic", "osd", "osd-hcp", "ipi", "prow-ci", "unknown"]:
        tc = type_counts.get(ct)
        if not tc or tc["resources"] == 0:
            continue
        expired_note = ""
        if tc["expired_clusters"]:
            expired_note = f", {len(tc['expired_clusters'])} expired"
        lines.append(
            f"  {_type_label(ct):<15} {tc['resources']:>4} resources  "
            f"({len(tc['clusters'])} clusters{expired_note})"
        )

    prunable_count = sum(1 for t in all_traces if t.prunability == "prunable")
    questionable_count = sum(1 for t in all_traces if t.prunability == "questionable")
    not_prunable = len(all_traces) - prunable_count - questionable_count
    lines.append("")
    lines.append("Prunability (all scanned):")
    lines.append(f"  Prunable:        {prunable_count:>4} resources")
    lines.append(f"  Questionable:    {questionable_count:>4} resources")
    lines.append(f"  Not prunable:    {not_prunable:>4} resources")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="AWS Resource Tracer — trace resources to clusters and owners (read-only)"
    )
    parser.add_argument(
        "resource_ids", nargs="*",
        help="Resource IDs to trace (e.g., i-xxx for EC2, nat-xxx for NAT gateways)"
    )
    parser.add_argument(
        "--region", default="us-east-1",
        help="AWS region (default: us-east-1)"
    )
    all_types = ",".join(RESOURCE_TYPE_LABELS.keys())
    parser.add_argument(
        "--resource-type", default=all_types,
        help=f"Comma-separated resource types to trace (default: {all_types})"
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Scan all resources of the given type in the region"
    )
    parser.add_argument(
        "--states", default=None,
        help="Comma-separated states for --scan (default depends on resource type)"
    )
    parser.add_argument(
        "--ocm-accounts", default="PROD,STAGE",
        help="Comma-separated OCM accounts to query (default: PROD,STAGE)"
    )
    parser.add_argument(
        "--skip-ocm", action="store_true",
        help="Skip OCM lookups"
    )
    parser.add_argument(
        "--output", default="text", choices=["text", "json"],
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--filter", default=None,
        choices=["prunable", "questionable", "prunable+questionable", "not-prunable"],
        help="Filter output by prunability category"
    )
    parser.add_argument(
        "--age-threshold", type=int, default=30,
        help="Days after which a resource is considered questionably prunable (default: 30)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.scan and not args.resource_ids:
        print("Error: provide resource IDs or use --scan")
        sys.exit(1)

    json_mode = args.output == "json"
    if json_mode:
        real_stdout = sys.stdout
        sys.stdout = sys.stderr

    try:
        ec2_client = boto3.Session(region_name=args.region).client("ec2")
    except NoCredentialsError:
        print("Error: AWS credentials not found.")
        sys.exit(1)

    skip_ocm = args.skip_ocm
    ocm_accounts = args.ocm_accounts.split(",")
    resource_types = args.resource_type.split(",")

    for rt in resource_types:
        if rt not in RESOURCE_TYPE_LABELS:
            print(f"Error: unknown resource type '{rt}'. Valid types: {', '.join(RESOURCE_TYPE_LABELS.keys())}")
            sys.exit(1)

    if not args.scan and len(resource_types) > 1:
        print("Error: specify a single --resource-type when passing resource IDs")
        sys.exit(1)

    traces = []

    fetch_classify = {
        "ec2": (fetch_ec2_instances, classify_ec2_instance, "instances"),
        "nat-gateway": (fetch_nat_gateways, classify_nat_gateway, "NAT gateways"),
        "vpc-endpoint": (fetch_vpc_endpoints, classify_vpc_endpoint, "VPC endpoints"),
        "eip": (fetch_eips, classify_eip, "Elastic IPs"),
    }

    for rt in resource_types:
        fetch_fn, classify_fn, label = fetch_classify[rt]
        state_list = (args.states or DEFAULT_STATES[rt]).split(",")

        if args.scan:
            if state_list == ["all"]:
                print(f"Scanning {args.region} for {label}")
            else:
                print(f"Scanning {args.region} for {label} in states: {', '.join(state_list)}")
            resources = fetch_fn(ec2_client, states=state_list)
        else:
            resources = fetch_fn(ec2_client, args.resource_ids)
        print(f"Found {len(resources)} {label}")
        traces.extend(classify_fn(r) for r in resources)

    ocm_clusters = {}
    owner_cache = {}
    if not skip_ocm:
        print("Loading OCM cluster data...")
        ocm_clusters = load_ocm_clusters(ocm_accounts)
        print(f"Loaded {len(ocm_clusters) // 2} clusters from OCM")
        enrich_with_ocm(traces, ocm_clusters, owner_cache)

    classify_prunability(traces, ocm_clusters, age_threshold=args.age_threshold)
    all_traces = traces
    traces = filter_traces(traces, args.filter)

    if json_mode:
        sys.stdout = real_stdout
        print(format_json_report(traces))
    else:
        print(format_text_report(traces, args.region, resource_types, all_traces))


if __name__ == "__main__":
    main()
