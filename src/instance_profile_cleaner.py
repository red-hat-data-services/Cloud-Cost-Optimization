"""
AWS Instance Profile Cleanup Utility

This script identifies and deletes expired instance profiles based on specific tags:
- expirationDate: Instance profile expiration date in YYYY-MM-DD format
- prow.k8s.io/job: Indicates CI/CD related instance profiles

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


class InstanceProfileCleaner:
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

    def get_all_instance_profiles(self):
        """Fetch all instance profiles in the account."""
        instance_profiles = []
        try:
            paginator = self.iam_client.get_paginator('list_instance_profiles')
            for page in paginator.paginate():
                instance_profiles.extend(page['InstanceProfiles'])
            print(f"Found {len(instance_profiles)} total instance profiles")
            return instance_profiles
        except ClientError as e:
            print(f"Error fetching instance profiles: {e}")
            return []

    def get_instance_profile_tags(self, instance_profile_name):
        """Get tags for a specific instance profile."""
        try:
            time.sleep(1)
            response = self.iam_client.list_instance_profile_tags(InstanceProfileName=instance_profile_name)
            return {tag['Key']: tag['Value'] for tag in response['Tags']}
        except ClientError as e:
            print(f"Warning: Could not fetch tags for instance profile {instance_profile_name}: {e}")
            return {}

    def filter_tagged_instance_profiles(self, instance_profiles):
        """Filter instance profiles that have the required tags."""
        tagged_profiles = []
        required_tags = ['expirationDate', 'prow.k8s.io/job']

        for profile in instance_profiles:
            profile_name = profile['InstanceProfileName']
            tags = self.get_instance_profile_tags(profile_name)

            # Check if instance profile has both required tags
            has_required_tags = all(tag in tags for tag in required_tags)

            if has_required_tags:
                profile['Tags'] = tags
                tagged_profiles.append(profile)

        print(f"Found {len(tagged_profiles)} instance profiles with required tags")
        return tagged_profiles

    def calculate_expired_instance_profiles(self, tagged_profiles, expiration_days=2):
        """Calculate which instance profiles have expired by at least the specified number of days."""
        expired_profiles = []
        current_date = datetime.now().date()

        for profile in tagged_profiles:
            expiration_date_str = profile['Tags'].get('expirationDate')

            try:
                # Parse ISO format with timezone: "2025-09-05T01:06+00:00"
                expiration_datetime = datetime.fromisoformat(expiration_date_str.replace('Z', '+00:00'))
                expiration_date = expiration_datetime.date()
                days_expired = (current_date - expiration_date).days

                if days_expired >= expiration_days:
                    profile['DaysExpired'] = days_expired
                    expired_profiles.append(profile)
                    print(f"Instance profile {profile['InstanceProfileName']} expired {days_expired} days ago")

            except (ValueError, TypeError):
                print(f"Warning: Invalid expiration date format for instance profile {profile['InstanceProfileName']}: {expiration_date_str}")
                continue

        print(f"Found {len(expired_profiles)} instance profiles expired by {expiration_days}+ days")
        return expired_profiles

    def delete_instance_profile(self, profile_name, roles):
        """Delete an instance profile and remove role associations."""
        try:
            # First, remove all roles from the instance profile
            for role in roles:
                role_name = role['RoleName']
                self.iam_client.remove_role_from_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role_name
                )
                print(f"  Removed role {role_name} from instance profile {profile_name}")

            # Finally, delete the instance profile
            self.iam_client.delete_instance_profile(InstanceProfileName=profile_name)
            print(f"  Successfully deleted instance profile: {profile_name}")
            return True

        except ClientError as e:
            print(f"  Error deleting instance profile {profile_name}: {e}")
            return False

    def process_expired_instance_profiles(self, expired_profiles):
        """Process expired instance profiles - either print them (dry-run) or delete them."""
        if not expired_profiles:
            print("No expired instance profiles found.")
            return

        if self.dry_run:
            print("\n=== DRY RUN MODE - No instance profiles will be deleted ===")
            print("The following instance profiles would be deleted:")
            for profile in expired_profiles:
                role_names = [role['RoleName'] for role in profile.get('Roles', [])]
                print(f"  - {profile['InstanceProfileName']} (expired {profile['DaysExpired']} days ago)")
                print(f"    Expiration Date: {profile['Tags']['expirationDate']}")
                print(f"    Prow Job: {profile['Tags']['prow.k8s.io/job']}")
                if role_names:
                    print(f"    Associated Roles: {', '.join(role_names)}")
            print(f"\nTotal instance profiles eligible for deletion: {len(expired_profiles)}")
            print("Run with --dry-run false to perform actual deletion.")
        else:
            print(f"\n=== EXECUTING DELETION - {len(expired_profiles)} instance profiles will be deleted ===")

            # Ask for confirmation
            # response = input(f"Are you sure you want to delete {len(expired_profiles)} instance profiles? (yes/no): ")
            # if response.lower() != 'yes':
            #     print("Deletion cancelled by user.")
            #     return

            success_count = 0
            for profile in expired_profiles:
                print(f"Deleting instance profile: {profile['InstanceProfileName']}")
                if self.delete_instance_profile(profile['InstanceProfileName'], profile.get('Roles', [])):
                    success_count += 1

            print(f"\nDeletion completed: {success_count}/{len(expired_profiles)} instance profiles deleted successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="AWS Instance Profile Cleanup Utility - Identifies and deletes expired instance profiles"
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

    print("AWS Instance Profile Cleanup Utility")
    print("=" * 40)

    # Initialize cleaner
    dry_run = args.dry_run == 'true'
    cleaner = InstanceProfileCleaner(region_name=args.region, dry_run=dry_run)

    # Process instance profiles
    all_profiles = cleaner.get_all_instance_profiles()
    tagged_profiles = cleaner.filter_tagged_instance_profiles(all_profiles)
    expired_profiles = cleaner.calculate_expired_instance_profiles(tagged_profiles, args.days)
    cleaner.process_expired_instance_profiles(expired_profiles)


if __name__ == "__main__":
    main()
