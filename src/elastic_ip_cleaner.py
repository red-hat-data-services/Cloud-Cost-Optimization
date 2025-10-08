"""
AWS Elastic IP Cleanup Utility

This script identifies and releases expired Elastic IPs based on specific tags:
- expirationDate: Elastic IP expiration date in YYYY-MM-DD format
- prow.k8s.io/job: Indicates CI/CD related Elastic IPs

To run this script, you need to install the following dependencies:
pip install boto3
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


class ElasticIPCleaner:
    def __init__(self, region_name="us-west-2", dry_run=True):
        self.dry_run = dry_run
        self.region_name = region_name
        try:
            self.session = boto3.Session(region_name=region_name)
            self.ec2_client = self.session.client("ec2")
            print(f"Initialized EC2 client for region: {region_name}")
        except NoCredentialsError:
            print("Error: AWS credentials not found. Please configure AWS credentials.")
            sys.exit(1)
        except Exception as e:
            print(f"Error initializing AWS session: {e}")
            sys.exit(1)

    def get_all_elastic_ips(self):
        """Fetch all Elastic IPs in the region."""
        elastic_ips = []
        try:
            response = self.ec2_client.describe_addresses()
            elastic_ips = response['Addresses']
            print(f"Found {len(elastic_ips)} total Elastic IPs in {self.region_name}")
            return elastic_ips
        except ClientError as e:
            print(f"Error fetching Elastic IPs: {e}")
            return []

    def get_elastic_ip_tags(self, eip):
        """Get tags for a specific Elastic IP."""
        tags = eip.get('Tags', [])
        return {tag['Key']: tag['Value'] for tag in tags}

    def filter_tagged_elastic_ips(self, elastic_ips):
        """Filter Elastic IPs that have the required tags."""
        tagged_eips = []
        required_tags = ['expirationDate', 'prow.k8s.io/job']

        for eip in elastic_ips:
            tags = self.get_elastic_ip_tags(eip)
            time.sleep(0.2)
            # Check if Elastic IP has both required tags
            has_required_tags = all(tag in tags for tag in required_tags)

            if has_required_tags:
                eip['ParsedTags'] = tags
                tagged_eips.append(eip)

        print(f"Found {len(tagged_eips)} Elastic IPs with required tags")
        return tagged_eips

    def calculate_expired_elastic_ips(self, tagged_eips, expiration_days=2):
        """Calculate which Elastic IPs have expired by at least the specified number of days."""
        expired_eips = []
        current_date = datetime.now().date()

        for eip in tagged_eips:
            expiration_date_str = eip['ParsedTags'].get('expirationDate')
            eip_identifier = eip.get('PublicIp', eip.get('AllocationId', 'Unknown'))

            try:
                # Parse ISO format with timezone: "2025-09-05T01:06+00:00"
                expiration_datetime = datetime.fromisoformat(expiration_date_str.replace('Z', '+00:00'))
                expiration_date = expiration_datetime.date()
                days_expired = (current_date - expiration_date).days

                if days_expired >= expiration_days:
                    eip['DaysExpired'] = days_expired
                    expired_eips.append(eip)
                    print(f"Elastic IP {eip_identifier} expired {days_expired} days ago")

            except (ValueError, TypeError):
                print(f"Warning: Invalid expiration date format for Elastic IP {eip_identifier}: {expiration_date_str}")
                continue

        print(f"Found {len(expired_eips)} Elastic IPs expired by {expiration_days}+ days")
        return expired_eips

    def release_elastic_ip(self, eip):
        """Release an Elastic IP."""
        eip_identifier = eip.get('PublicIp', 'Unknown')
        allocation_id = eip.get('AllocationId')
        association_id = eip.get('AssociationId')

        try:
            # If the EIP is associated with an instance or network interface, disassociate it first
            if association_id:
                print(f"  Disassociating Elastic IP {eip_identifier} (Association ID: {association_id})")
                self.ec2_client.disassociate_address(AssociationId=association_id)
                print(f"  Successfully disassociated Elastic IP {eip_identifier}")

            # Release the Elastic IP
            if allocation_id:
                self.ec2_client.release_address(AllocationId=allocation_id)
                print(f"  Successfully released Elastic IP: {eip_identifier} (Allocation ID: {allocation_id})")
            else:
                # For EC2-Classic (rare case)
                public_ip = eip.get('PublicIp')
                if public_ip:
                    self.ec2_client.release_address(PublicIp=public_ip)
                    print(f"  Successfully released Elastic IP: {eip_identifier}")
                else:
                    print(f"  Error: No AllocationId or PublicIp found for Elastic IP")
                    return False

            return True

        except ClientError as e:
            print(f"  Error releasing Elastic IP {eip_identifier}: {e}")
            return False

    def process_expired_elastic_ips(self, expired_eips):
        """Process expired Elastic IPs - either print them (dry-run) or release them."""
        if not expired_eips:
            print("No expired Elastic IPs found.")
            return

        if self.dry_run:
            print("\n=== DRY RUN MODE - No Elastic IPs will be released ===")
            print("The following Elastic IPs would be released:")
            for eip in expired_eips:
                eip_identifier = eip.get('PublicIp', eip.get('AllocationId', 'Unknown'))
                allocation_id = eip.get('AllocationId', 'N/A')
                association_id = eip.get('AssociationId', 'Not associated')
                instance_id = eip.get('InstanceId', 'N/A')

                print(f"  - {eip_identifier} (expired {eip['DaysExpired']} days ago)")
                print(f"    Allocation ID: {allocation_id}")
                print(f"    Expiration Date: {eip['ParsedTags']['expirationDate']}")
                print(f"    Prow Job: {eip['ParsedTags']['prow.k8s.io/job']}")
                print(f"    Association ID: {association_id}")
                if instance_id != 'N/A':
                    print(f"    Associated Instance: {instance_id}")
            print(f"\nTotal Elastic IPs eligible for release: {len(expired_eips)}")
            print("Run with --dry-run false to perform actual release.")
        else:
            print(f"\n=== EXECUTING RELEASE - {len(expired_eips)} Elastic IPs will be released ===")

            # Ask for confirmation
            # response = input(f"Are you sure you want to release {len(expired_eips)} Elastic IPs? (yes/no): ")
            # if response.lower() != 'yes':
            #     print("Release cancelled by user.")
            #     return

            success_count = 0
            for eip in expired_eips:
                eip_identifier = eip.get('PublicIp', eip.get('AllocationId', 'Unknown'))
                print(f"Releasing Elastic IP: {eip_identifier}")
                if self.release_elastic_ip(eip):
                    success_count += 1

            print(f"\nRelease completed: {success_count}/{len(expired_eips)} Elastic IPs released successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Elastic IP Cleanup Utility - Identifies and releases expired Elastic IPs"
    )
    parser.add_argument(
        '--dry-run',
        type=str,
        choices=['true', 'false'],
        default='true',
        help='Dry-run mode: true or false (default: true)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=2,
        help='Minimum days since expiration for release (default: 2)'
    )
    parser.add_argument(
        '--region',
        default='us-west-2',
        help='AWS region (default: us-west-2)'
    )

    args = parser.parse_args()

    print("AWS Elastic IP Cleanup Utility")
    print("=" * 40)

    # Initialize cleaner
    dry_run = args.dry_run == 'true'
    cleaner = ElasticIPCleaner(region_name=args.region, dry_run=dry_run)

    # Process Elastic IPs
    all_eips = cleaner.get_all_elastic_ips()
    tagged_eips = cleaner.filter_tagged_elastic_ips(all_eips)
    expired_eips = cleaner.calculate_expired_elastic_ips(tagged_eips, args.days)
    cleaner.process_expired_elastic_ips(expired_eips)


if __name__ == "__main__":
    main()
