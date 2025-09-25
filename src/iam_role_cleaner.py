"""
AWS IAM Role Cleanup Utility

This script identifies and deletes expired IAM roles based on specific tags:
- expirationDate: Role expiration date in YYYY-MM-DD format
- prow.k8s.io/job: Indicates CI/CD related roles

To run this script, you need to install the following dependencies:
pip install boto3
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


class IAMRoleCleaner:
    def __init__(self, region_name="us-east-1", dry_run=True):
        self.dry_run = dry_run
        try:
            self.session = boto3.Session(region_name=region_name)
            self.iam_client = self.session.client("iam")
            print(f"Initialized IAM client for region: {region_name}")
        except NoCredentialsError:
            print("Error: AWS credentials not found. Please configure AWS credentials.")
            sys.exit(1)
        except Exception as e:
            print(f"Error initializing AWS session: {e}")
            sys.exit(1)

    def get_all_iam_roles(self):
        """Fetch all IAM roles in the account."""
        roles = []
        try:
            paginator = self.iam_client.get_paginator('list_roles')
            for page in paginator.paginate():
                roles.extend(page['Roles'])
            print(f"Found {len(roles)} total IAM roles")
            return roles
        except ClientError as e:
            print(f"Error fetching IAM roles: {e}")
            return []

    def get_role_tags(self, role_name):
        """Get tags for a specific IAM role."""
        try:
            response = self.iam_client.list_role_tags(RoleName=role_name)
            return {tag['Key']: tag['Value'] for tag in response['Tags']}
        except ClientError as e:
            print(f"Warning: Could not fetch tags for role {role_name}: {e}")
            return {}

    def filter_tagged_roles(self, roles):
        """Filter roles that have the required tags."""
        tagged_roles = []
        required_tags = ['expirationDate', 'prow.k8s.io/job']

        for role in roles:
            role_name = role['RoleName']
            tags = self.get_role_tags(role_name)

            # Check if role has both required tags
            has_required_tags = all(tag in tags for tag in required_tags)

            if has_required_tags:
                role['Tags'] = tags
                tagged_roles.append(role)

        print(f"Found {len(tagged_roles)} roles with required tags")
        return tagged_roles

    def calculate_expired_roles(self, tagged_roles, expiration_days=2):
        """Calculate which roles have expired by at least the specified number of days."""
        expired_roles = []
        current_date = datetime.now().date()

        for role in tagged_roles:
            expiration_date_str = role['Tags'].get('expirationDate')

            try:
                # Parse ISO format with timezone: "2025-09-05T01:06+00:00"
                expiration_datetime = datetime.fromisoformat(expiration_date_str.replace('Z', '+00:00'))
                expiration_date = expiration_datetime.date()
                days_expired = (current_date - expiration_date).days

                if days_expired >= expiration_days:
                    role['DaysExpired'] = days_expired
                    expired_roles.append(role)
                    print(f"Role {role['RoleName']} expired {days_expired} days ago")

            except (ValueError, TypeError):
                print(f"Warning: Invalid expiration date format for role {role['RoleName']}: {expiration_date_str}")
                continue

        print(f"Found {len(expired_roles)} roles expired by {expiration_days}+ days")
        return expired_roles

    def delete_iam_role(self, role_name):
        """Delete an IAM role and its associated policies."""
        try:
            # First, detach all managed policies
            response = self.iam_client.list_attached_role_policies(RoleName=role_name)
            for policy in response['AttachedPolicies']:
                self.iam_client.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy['PolicyArn']
                )
                print(f"  Detached policy {policy['PolicyName']} from role {role_name}")

            # Delete all inline policies
            response = self.iam_client.list_role_policies(RoleName=role_name)
            for policy_name in response['PolicyNames']:
                self.iam_client.delete_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name
                )
                print(f"  Deleted inline policy {policy_name} from role {role_name}")

            # Finally, delete the role
            self.iam_client.delete_role(RoleName=role_name)
            print(f"  Successfully deleted role: {role_name}")
            return True

        except ClientError as e:
            print(f"  Error deleting role {role_name}: {e}")
            return False

    def process_expired_roles(self, expired_roles):
        """Process expired roles - either print them (dry-run) or delete them."""
        if not expired_roles:
            print("No expired roles found.")
            return

        if self.dry_run:
            print("\n=== DRY RUN MODE - No roles will be deleted ===")
            print("The following roles would be deleted:")
            for role in expired_roles:
                print(f"  - {role['RoleName']} (expired {role['DaysExpired']} days ago)")
                print(f"    Expiration Date: {role['Tags']['expirationDate']}")
                print(f"    Prow Job: {role['Tags']['prow.k8s.io/job']}")
            print(f"\nTotal roles eligible for deletion: {len(expired_roles)}")
            print("Run with --dry-run false to perform actual deletion.")
        else:
            print(f"\n=== EXECUTING DELETION - {len(expired_roles)} roles will be deleted ===")

            # Ask for confirmation
            # response = input(f"Are you sure you want to delete {len(expired_roles)} IAM roles? (yes/no): ")
            # if response.lower() != 'yes':
            #     print("Deletion cancelled by user.")
            #     return

            success_count = 0
            for role in expired_roles:
                print(f"Deleting role: {role['RoleName']}")
                if self.delete_iam_role(role['RoleName']):
                    success_count += 1

            print(f"\nDeletion completed: {success_count}/{len(expired_roles)} roles deleted successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="AWS IAM Role Cleanup Utility - Identifies and deletes expired IAM roles"
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
        help='Minimum days since expiration for deletion (default: 2)'
    )
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='AWS region (default: us-east-1)'
    )

    args = parser.parse_args()

    print("AWS IAM Role Cleanup Utility")
    print("=" * 40)

    # Initialize cleaner
    dry_run = args.dry_run == 'true'
    cleaner = IAMRoleCleaner(region_name=args.region, dry_run=dry_run)

    # Process roles
    all_roles = cleaner.get_all_iam_roles()
    tagged_roles = cleaner.filter_tagged_roles(all_roles)
    expired_roles = cleaner.calculate_expired_roles(tagged_roles, args.days)
    cleaner.process_expired_roles(expired_roles)


if __name__ == "__main__":
    main()