#!/usr/bin/env python3

import boto3
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any
import time

class AWSResourceCleaner:
    def __init__(self, region: str = 'us-west-2'):
        self.region = region
        self.ec2 = boto3.client('ec2', region_name=region)
        self.iam = boto3.client('iam')
        
    def get_expired_vpcs(self) -> List[Dict[str, Any]]:
        """Find VPCs with prow.k8s.io/build-id tag and expired expirationDate"""
        try:
            response = self.ec2.describe_vpcs()
            expired_vpcs = []
            cutoff_date = datetime.now() - timedelta(hours=5)
            
            for vpc in response['Vpcs']:
                tags = {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}
                
                # Check for required tags
                if 'prow.k8s.io/build-id' not in tags or 'expirationDate' not in tags:
                    continue
                
                # Parse expiration date
                try:
                    expiration_date = datetime.fromisoformat(tags['expirationDate'].replace('Z', '+00:00'))
                    if expiration_date.replace(tzinfo=None) < cutoff_date:
                        expired_vpcs.append({
                            'VpcId': vpc['VpcId'],
                            'ExpirationDate': tags['expirationDate'],
                            'BuildId': tags['prow.k8s.io/build-id']
                        })
                except ValueError as e:
                    print(f"Invalid date format for VPC {vpc['VpcId']}: {tags['expirationDate']}")
                    
            return expired_vpcs
        except Exception as e:
            print(f"Error finding expired VPCs: {e}")
            return []
    
    def get_expired_oidc_providers(self) -> List[Dict[str, Any]]:
        """Find OIDC providers with prow.k8s.io/build-id tag and expired expirationDate"""
        try:
            response = self.iam.list_open_id_connect_providers()
            expired_providers = []
            cutoff_date = datetime.now() - timedelta(hours=5)
            
            for provider in response['OpenIDConnectProviderList']:
                arn = provider['Arn']
                
                # Get provider tags
                try:
                    tags_response = self.iam.list_open_id_connect_provider_tags(OpenIDConnectProviderArn=arn)
                    tags = {tag['Key']: tag['Value'] for tag in tags_response.get('Tags', [])}
                    
                    # Check for required tags
                    if 'prow.k8s.io/build-id' not in tags or 'expirationDate' not in tags:
                        continue
                    
                    # Parse expiration date
                    try:
                        expiration_date = datetime.fromisoformat(tags['expirationDate'].replace('Z', '+00:00'))
                        if expiration_date.replace(tzinfo=None) < cutoff_date:
                            expired_providers.append({
                                'Arn': arn,
                                'ExpirationDate': tags['expirationDate'],
                                'BuildId': tags['prow.k8s.io/build-id']
                            })
                    except ValueError as e:
                        print(f"Invalid date format for OIDC provider {arn}: {tags['expirationDate']}")
                        
                except Exception as e:
                    print(f"Error getting tags for OIDC provider {arn}: {e}")
                    
            return expired_providers
        except Exception as e:
            print(f"Error finding expired OIDC providers: {e}")
            return []
    
    def _get_vpc_build_id(self, vpc_id: str) -> str:
        """Get the prow.k8s.io/build-id tag value for a VPC"""
        try:
            response = self.ec2.describe_vpcs(VpcIds=[vpc_id])
            if response['Vpcs']:
                tags = {tag['Key']: tag['Value'] for tag in response['Vpcs'][0].get('Tags', [])}
                return tags.get('prow.k8s.io/build-id', '')
        except Exception as e:
            print(f"Error getting VPC build-id for {vpc_id}: {e}")
        return ''
    
    def _validate_resource_build_id(self, resource_tags: List[Dict[str, str]], vpc_build_id: str, resource_id: str, resource_type: str) -> bool:
        """Validate that a resource has the same build-id tag as its parent VPC"""
        if not vpc_build_id:
            print(f"    Warning: VPC has no build-id tag, skipping {resource_type} {resource_id}")
            return False
            
        tags = {tag['Key']: tag['Value'] for tag in resource_tags}
        resource_build_id = tags.get('prow.k8s.io/build-id', '')

        bypass_validation = ['Security Group', 'Classic Load Balancer', "Route Table"]

        if not resource_build_id:
            if resource_type in bypass_validation:
                print(f"    Warning: {resource_type} {resource_id} has no prow.k8s.io/build-id tag, but proceeding with deletion")
                return True
            print(f"    Warning: {resource_type} {resource_id} has no prow.k8s.io/build-id tag, skipping deletion")
            return False
            
        if resource_build_id != vpc_build_id:
            print(f"    Warning: {resource_type} {resource_id} build-id ({resource_build_id}) doesn't match VPC build-id ({vpc_build_id}), skipping deletion")
            return False
            
        return True
    
    def delete_vpc_dependencies(self, vpc_id: str, dry_run: bool = False) -> bool:
        """Delete all resources within a VPC before deleting the VPC itself"""
        try:
            if dry_run:
                print(f"[DRY RUN] Would clean up dependencies for VPC {vpc_id}...")
            else:
                print(f"Cleaning up dependencies for VPC {vpc_id}...")
            
            # Get VPC's build-id for validation
            vpc_build_id = self._get_vpc_build_id(vpc_id)
            
            # Delete NAT Gateways
            self._delete_nat_gateways(vpc_id, vpc_build_id, dry_run)
            
            # Delete instances
            self._delete_instances(vpc_id, vpc_build_id, dry_run)
            
            # Delete load balancers
            self._delete_load_balancers(vpc_id, vpc_build_id, dry_run)
            
            # Delete network interfaces
            self._delete_network_interfaces(vpc_id, vpc_build_id, dry_run)
            
            # Delete security groups (except default)
            self._delete_security_groups(vpc_id, vpc_build_id, dry_run)

            # Delete subnets
            self._delete_subnets(vpc_id, vpc_build_id, dry_run)
            
            # Delete route tables (except main)
            self._delete_route_tables(vpc_id, vpc_build_id, dry_run)
            
            # Release Elastic IP addresses before deleting internet gateways
            self._release_elastic_ips(vpc_id, vpc_build_id, dry_run)
              
            # Delete internet gateways
            self._delete_internet_gateways(vpc_id, vpc_build_id, dry_run)
            
            # Delete VPC endpoints
            self._delete_vpc_endpoints(vpc_id, vpc_build_id, dry_run)
            
            return True
        except Exception as e:
            print(f"Error cleaning dependencies for VPC {vpc_id}: {e}")
            return False
    
    def _delete_instances(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Terminate all EC2 instances in the VPC"""
        try:
            response = self.ec2.describe_instances(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            instance_ids = []
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    if instance['State']['Name'] not in ['terminated', 'terminating']:
                        # Validate build-id tag before adding to deletion list
                        if self._validate_resource_build_id(
                            instance.get('Tags', []), 
                            vpc_build_id, 
                            instance['InstanceId'], 
                            'Instance'
                        ):
                            instance_ids.append(instance['InstanceId'])
            
            if instance_ids:
                if dry_run:
                    print(f"  [DRY RUN] Would terminate {len(instance_ids)} instances...")
                else:
                    print(f"  Terminating {len(instance_ids)} instances...")
                    self.ec2.terminate_instances(InstanceIds=instance_ids)
                    
                    # Wait for instances to terminate
                    waiter = self.ec2.get_waiter('instance_terminated')
                    waiter.wait(InstanceIds=instance_ids, WaiterConfig={'Delay': 15, 'MaxAttempts': 40})
                
        except Exception as e:
            print(f"  Error deleting instances: {e}")
    
    def _delete_load_balancers(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all load balancers in the VPC"""
        try:
            # Classic Load Balancers
            elb = boto3.client('elb', region_name=self.region)
            response = elb.describe_load_balancers()
            
            for lb in response['LoadBalancerDescriptions']:
                if lb['VPCId'] == vpc_id:
                    # Get tags for classic load balancer
                    try:
                        tags_response = elb.describe_tags(LoadBalancerNames=[lb['LoadBalancerName']])
                        tags = []
                        if tags_response['TagDescriptions']:
                            tags = tags_response['TagDescriptions'][0].get('Tags', [])
                        
                        # Validate build-id tag before deleting
                        if self._validate_resource_build_id(
                            tags, 
                            vpc_build_id, 
                            lb['LoadBalancerName'], 
                            'Classic Load Balancer'
                        ):
                            if dry_run:
                                print(f"  [DRY RUN] Would delete classic load balancer {lb['LoadBalancerName']}...")
                            else:
                                print(f"  Deleting classic load balancer {lb['LoadBalancerName']}...")
                                elb.delete_load_balancer(LoadBalancerName=lb['LoadBalancerName'])
                    except Exception as tag_error:
                        print(f"  Warning: Could not get tags for classic load balancer {lb['LoadBalancerName']}: {tag_error}")
            
            # Application/Network Load Balancers
            elbv2 = boto3.client('elbv2', region_name=self.region)
            response = elbv2.describe_load_balancers()
            
            for lb in response['LoadBalancers']:
                if lb['VpcId'] == vpc_id:
                    # Get tags for ALB/NLB
                    try:
                        tags_response = elbv2.describe_tags(ResourceArns=[lb['LoadBalancerArn']])
                        tags = []
                        if tags_response['TagDescriptions']:
                            tags = tags_response['TagDescriptions'][0].get('Tags', [])
                        
                        # Validate build-id tag before deleting
                        if self._validate_resource_build_id(
                            tags, 
                            vpc_build_id, 
                            lb['LoadBalancerName'], 
                            'Load Balancer'
                        ):
                            if dry_run:
                                print(f"  [DRY RUN] Would delete load balancer {lb['LoadBalancerName']}...")
                            else:
                                print(f"  Deleting load balancer {lb['LoadBalancerName']}...")
                                elbv2.delete_load_balancer(LoadBalancerArn=lb['LoadBalancerArn'])
                    except Exception as tag_error:
                        print(f"  Warning: Could not get tags for load balancer {lb['LoadBalancerName']}: {tag_error}")
                    
        except Exception as e:
            print(f"  Error deleting load balancers: {e}")
    
    def _delete_nat_gateways(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all NAT gateways in the VPC"""
        try:
            response = self.ec2.describe_nat_gateways(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            nat_gateways_to_delete = []
            for nat_gw in response['NatGateways']:
                if nat_gw['State'] not in ['deleted', 'deleting']:
                    # Validate build-id tag before adding to deletion list
                    if self._validate_resource_build_id(
                        nat_gw.get('Tags', []), 
                        vpc_build_id, 
                        nat_gw['NatGatewayId'], 
                        'NAT Gateway'
                    ):
                        nat_gateways_to_delete.append(nat_gw['NatGatewayId'])
                        if dry_run:
                            print(f"  [DRY RUN] Would delete NAT gateway {nat_gw['NatGatewayId']}...")
                        else:
                            print(f"  Deleting NAT gateway {nat_gw['NatGatewayId']}...")
                            self.ec2.delete_nat_gateway(NatGatewayId=nat_gw['NatGatewayId'])
            
            # Wait for NAT gateways to be deleted
            if nat_gateways_to_delete and not dry_run:
                print("  Waiting for NAT gateways to be deleted...")
                time.sleep(60)
                
        except Exception as e:
            print(f"  Error deleting NAT gateways: {e}")
    
    def _delete_network_interfaces(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all available network interfaces in the VPC"""
        try:
            response = self.ec2.describe_network_interfaces(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for ni in response['NetworkInterfaces']:
                if ni['Status'] == 'available':
                    # Validate build-id tag before deleting
                    if self._validate_resource_build_id(
                        ni.get('TagSet', []), 
                        vpc_build_id, 
                        ni['NetworkInterfaceId'], 
                        'Network Interface'
                    ):
                        if dry_run:
                            print(f"  [DRY RUN] Would delete network interface {ni['NetworkInterfaceId']}...")
                        else:
                            print(f"  Deleting network interface {ni['NetworkInterfaceId']}...")
                            try:
                                self.ec2.delete_network_interface(NetworkInterfaceId=ni['NetworkInterfaceId'])
                            except Exception as e:
                                print(f"    Could not delete network interface {ni['NetworkInterfaceId']}: {e}")
                        
        except Exception as e:
            print(f"  Error deleting network interfaces: {e}")
    
    def _delete_security_groups(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all security groups except the default one"""
        try:
            response = self.ec2.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            # First pass: Remove all rules that reference other security groups
            security_groups = [sg for sg in response['SecurityGroups'] if sg['GroupName'] != 'default']
            
            has_rules_to_delete = False
            for sg in security_groups:
                if self._validate_resource_build_id(
                    sg.get('Tags', []), 
                    vpc_build_id, 
                    sg['GroupId'], 
                    'Security Group'
                ):
                    # Remove ingress rules
                    if sg['IpPermissions']:
                        if dry_run:
                            print(f"  [DRY RUN] Would remove ingress rules from security group {sg['GroupId']}...")
                        else:
                            print(f"  Removing ingress rules from security group {sg['GroupId']}...")
                            try:
                                has_rules_to_delete = True
                                self.ec2.revoke_security_group_ingress(
                                    GroupId=sg['GroupId'],
                                    IpPermissions=sg['IpPermissions']
                                )
                            except Exception as e:
                                print(f"    Could not remove ingress rules from {sg['GroupId']}: {e}")
                    
                    # Remove egress rules (except default allow-all)
                    non_default_egress = [
                        perm for perm in sg['IpPermissionsEgress']
                        if not (perm.get('IpProtocol') == '-1' and 
                               perm.get('IpRanges') == [{'CidrIp': '0.0.0.0/0'}])
                    ]
                    if non_default_egress:
                        if dry_run:
                            print(f"  [DRY RUN] Would remove egress rules from security group {sg['GroupId']}...")
                        else:
                            print(f"  Removing egress rules from security group {sg['GroupId']}...")
                            try:
                                has_rules_to_delete = True
                                self.ec2.revoke_security_group_egress(
                                    GroupId=sg['GroupId'],
                                    IpPermissions=non_default_egress
                                )
                            except Exception as e:
                                print(f"    Could not remove egress rules from {sg['GroupId']}: {e}")
                    if has_rules_to_delete: 
                        print("  Waiting for security group rules to be deleted...")
                        time.sleep(30)
            # Second pass: Delete the security groups
            for sg in security_groups:
                if self._validate_resource_build_id(
                    sg.get('Tags', []), 
                    vpc_build_id, 
                    sg['GroupId'], 
                    'Security Group'
                ):
                    if dry_run:
                        print(f"  [DRY RUN] Would delete security group {sg['GroupId']}...")
                    else:
                        print(f"  Deleting security group {sg['GroupId']}...")
                        try:
                            self.ec2.delete_security_group(GroupId=sg['GroupId'])
                        except Exception as e:
                            print(f"    Could not delete security group {sg['GroupId']}: {e}")
                        
        except Exception as e:
            print(f"  Error deleting security groups: {e}")
    
    def _delete_route_tables(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all route tables except the main one"""
        try:
            response = self.ec2.describe_route_tables(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for rt in response['RouteTables']:
                # Don't delete main route table
                is_main = any(assoc.get('Main', False) for assoc in rt.get('Associations', []))
                if not is_main:
                    # Validate build-id tag before deleting
                    if self._validate_resource_build_id(
                        rt.get('Tags', []), 
                        vpc_build_id, 
                        rt['RouteTableId'], 
                        'Route Table'
                    ):
                        if dry_run:
                            print(f"  [DRY RUN] Would delete route table {rt['RouteTableId']}...")
                        else:
                            print(f"  Deleting route table {rt['RouteTableId']}...")
                            try:
                                self.ec2.delete_route_table(RouteTableId=rt['RouteTableId'])
                            except Exception as e:
                                print(f"    Could not delete route table {rt['RouteTableId']}: {e}")
                        
        except Exception as e:
            print(f"  Error deleting route tables: {e}")
    
    def _delete_subnets(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all subnets in the VPC"""
        try:
            response = self.ec2.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for subnet in response['Subnets']:
                # Validate build-id tag before deleting
                if self._validate_resource_build_id(
                    subnet.get('Tags', []), 
                    vpc_build_id, 
                    subnet['SubnetId'], 
                    'Subnet'
                ):
                    if dry_run:
                        print(f"  [DRY RUN] Would delete subnet {subnet['SubnetId']}...")
                    else:
                        print(f"  Deleting subnet {subnet['SubnetId']}...")
                        try:
                            self.ec2.delete_subnet(SubnetId=subnet['SubnetId'])
                        except Exception as e:
                            print(f"    Could not delete subnet {subnet['SubnetId']}: {e}")
                    
        except Exception as e:
            print(f"  Error deleting subnets: {e}")
    
    def _release_elastic_ips(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Release all Elastic IP addresses in the VPC"""
        try:
            response = self.ec2.describe_addresses(
                Filters=[{'Name': 'domain', 'Values': ['vpc']}]
            )
            
            has_address_to_delete = False
            for eip in response['Addresses']:
                # Check if EIP is associated with instances or NAT gateways in this VPC
                instance_id = eip.get('InstanceId')
                association_id = eip.get('AssociationId')
                
                # Check if the EIP belongs to this VPC by checking instance or NAT gateway
                belongs_to_vpc = False
                if instance_id:
                    # Check if instance is in this VPC
                    try:
                        instance_response = self.ec2.describe_instances(InstanceIds=[instance_id])
                        for reservation in instance_response['Reservations']:
                            for instance in reservation['Instances']:
                                if instance['VpcId'] == vpc_id:
                                    belongs_to_vpc = True
                                    break
                    except Exception:
                        pass
                
                if not belongs_to_vpc and 'NetworkInterfaceId' in eip:
                    # Check if network interface is in this VPC
                    try:
                        ni_response = self.ec2.describe_network_interfaces(
                            NetworkInterfaceIds=[eip['NetworkInterfaceId']]
                        )
                        for ni in ni_response['NetworkInterfaces']:
                            if ni['VpcId'] == vpc_id:
                                belongs_to_vpc = True
                                break
                    except Exception:
                        pass
                
                if belongs_to_vpc:
                    # Validate build-id tag before releasing
                    if self._validate_resource_build_id(
                        eip.get('Tags', []), 
                        vpc_build_id, 
                        eip['AllocationId'], 
                        'Elastic IP'
                    ):
                        if dry_run:
                            print(f"  [DRY RUN] Would release Elastic IP {eip['PublicIp']} ({eip['AllocationId']})...")
                        else:
                            print(f"  Releasing Elastic IP {eip['PublicIp']} ({eip['AllocationId']})...")
                            try:
                                has_address_to_delete = False
                                # Disassociate if associated
                                if association_id:
                                    self.ec2.disassociate_address(AssociationId=association_id)
                                # Release the address
                                self.ec2.release_address(AllocationId=eip['AllocationId'])
                            except Exception as e:
                                print(f"    Could not release Elastic IP {eip['AllocationId']}: {e}")
                
            if has_address_to_delete:
                print("  Waiting for elastic ip to be fully cleaned up...")
                time.sleep(30)
        except Exception as e:
            print(f"  Error releasing Elastic IPs: {e}")
    
    def _delete_internet_gateways(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Detach and delete internet gateways"""
        try:
            response = self.ec2.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
            )
            
            for igw in response['InternetGateways']:
                # Validate build-id tag before deleting
                if self._validate_resource_build_id(
                    igw.get('Tags', []), 
                    vpc_build_id, 
                    igw['InternetGatewayId'], 
                    'Internet Gateway'
                ):
                    if dry_run:
                        print(f"  [DRY RUN] Would detach and delete internet gateway {igw['InternetGatewayId']}...")
                    else:
                        print(f"  Detaching and deleting internet gateway {igw['InternetGatewayId']}...")
                        try:
                            self.ec2.detach_internet_gateway(
                                InternetGatewayId=igw['InternetGatewayId'],
                                VpcId=vpc_id
                            )
                            self.ec2.delete_internet_gateway(InternetGatewayId=igw['InternetGatewayId'])
                        except Exception as e:
                            print(f"    Could not delete internet gateway {igw['InternetGatewayId']}: {e}")
             
        except Exception as e:
            print(f"  Error deleting internet gateways: {e}")
    
    def _delete_vpc_endpoints(self, vpc_id: str, vpc_build_id: str, dry_run: bool = False):
        """Delete all VPC endpoints"""
        try:
            response = self.ec2.describe_vpc_endpoints(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            endpoint_ids = []
            for ep in response['VpcEndpoints']:
                # Validate build-id tag before adding to deletion list
                if self._validate_resource_build_id(
                    ep.get('Tags', []), 
                    vpc_build_id, 
                    ep['VpcEndpointId'], 
                    'VPC Endpoint'
                ):
                    endpoint_ids.append(ep['VpcEndpointId'])
            
            if endpoint_ids:
                if dry_run:
                    print(f"  [DRY RUN] Would delete {len(endpoint_ids)} VPC endpoints...")
                else:
                    print(f"  Deleting {len(endpoint_ids)} VPC endpoints...")
                    self.ec2.delete_vpc_endpoints(VpcEndpointIds=endpoint_ids)
                
        except Exception as e:
            print(f"  Error deleting VPC endpoints: {e}")
    
    def delete_vpc(self, vpc_id: str, dry_run: bool = False) -> bool:
        """Delete the VPC itself"""
        try:
            if dry_run:
                print(f"[DRY RUN] Would delete VPC {vpc_id}...")
            else:
                print(f"Deleting VPC {vpc_id}...")
                self.ec2.delete_vpc(VpcId=vpc_id)
                print(f"Successfully deleted VPC {vpc_id}")
            return True
        except Exception as e:
            print(f"Error deleting VPC {vpc_id}: {e}")
            return False
    
    def delete_oidc_provider(self, arn: str, dry_run: bool = False) -> bool:
        """Delete an OpenID Connect provider"""
        try:
            if dry_run:
                print(f"[DRY RUN] Would delete OIDC provider {arn}...")
            else:
                print(f"Deleting OIDC provider {arn}...")
                self.iam.delete_open_id_connect_provider(OpenIDConnectProviderArn=arn)
                print(f"Successfully deleted OIDC provider {arn}")
            return True
        except Exception as e:
            print(f"Error deleting OIDC provider {arn}: {e}")
            return False
    
    def cleanup_expired_vpcs(self, dry_run: bool = False):
        """Main method to find and delete expired VPCs"""
        print(f"Searching for expired VPCs in {self.region}...")
        
        expired_vpcs = self.get_expired_vpcs()
        
        if not expired_vpcs:
            print("No expired VPCs found.")
            return
        
        # Sort VPCs by expiration date (oldest first)
        expired_vpcs.sort(key=lambda vpc: datetime.fromisoformat(vpc['ExpirationDate'].replace('Z', '+00:00')))
        
        print(f"Found {len(expired_vpcs)} expired VPCs (sorted oldest to newest):")
        for vpc in expired_vpcs:
            print(f"  - {vpc['VpcId']} (Build ID: {vpc['BuildId']}, Expired: {vpc['ExpirationDate']})")
        
        if dry_run:
            print("\nDry run mode - no resources will be deleted.")
            # return
        
        print(f"\nProceeding to delete {len(expired_vpcs)} VPCs (oldest first)...")
        
        for vpc in expired_vpcs:
            vpc_id = vpc['VpcId']
            if dry_run:
                print(f"\n[DRY RUN] Processing VPC {vpc_id}...")
            else:
                print(f"\nProcessing VPC {vpc_id}...")
            
            # Delete dependencies first
            if self.delete_vpc_dependencies(vpc_id, dry_run):
                if not dry_run:
                    # Wait a bit for resources to be fully cleaned up
                    print("  Waiting for resources to be fully cleaned up...")
                    time.sleep(30)
                
                # Delete the VPC
                self.delete_vpc(vpc_id, dry_run)
            else:
                print(f"  Failed to clean up dependencies for VPC {vpc_id}, skipping VPC deletion")

    def cleanup_expired_oidc_providers(self, dry_run: bool = False):
        """Main method to find and delete expired OIDC providers"""
        print("Searching for expired OIDC providers...")
        
        expired_providers = self.get_expired_oidc_providers()
        
        if not expired_providers:
            print("No expired OIDC providers found.")
            return
        
        print(f"Found {len(expired_providers)} expired OIDC providers:")
        for provider in expired_providers:
            print(f"  - {provider['Arn']} (Build ID: {provider['BuildId']}, Expired: {provider['ExpirationDate']})")
        
        if dry_run:
            print("\nDry run mode - no resources will be deleted.")
            return
        
        print(f"\nProceeding to delete {len(expired_providers)} OIDC providers...")
        
        for provider in expired_providers:
            arn = provider['Arn']
            print(f"\nProcessing OIDC provider {arn}...")
            self.delete_oidc_provider(arn, dry_run)

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Delete expired AWS resources with prow.k8s.io/build-id tags')
    parser.add_argument('--region', default='us-west-2', help='AWS region (default: us-west-2)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted without actually deleting')
    parser.add_argument('--resource-type', choices=['vpcs', 'oidc', 'all'], default='all', 
                       help='Type of resources to clean up (default: all)')
    
    args = parser.parse_args()
    
    # Verify AWS credentials
    try:
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        print(f"Running as: {identity.get('Arn', 'Unknown')}")
    except Exception as e:
        print(f"Error: Cannot access AWS credentials: {e}")
        sys.exit(1)
    
    cleaner = AWSResourceCleaner(region=args.region)
    
    if args.resource_type in ['vpcs', 'all']:
        cleaner.cleanup_expired_vpcs(dry_run=args.dry_run)
    
    if args.resource_type in ['oidc', 'all']:
        if args.resource_type == 'all':
            print("\n" + "="*50 + "\n")
        cleaner.cleanup_expired_oidc_providers(dry_run=args.dry_run)

if __name__ == '__main__':
    main()
