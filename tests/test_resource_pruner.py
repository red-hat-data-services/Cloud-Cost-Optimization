import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import resource_pruner


class ResourcePrunerTest(unittest.TestCase):
    def test_waits_for_nat_gateway_to_release_eip(self):
        ec2 = Mock()
        ec2.describe_nat_gateways.side_effect = [
            {"NatGateways": [{"NatGatewayId": "nat-1", "State": "deleting"}]},
            {"NatGateways": [{"NatGatewayId": "nat-1", "State": "deleted"}]},
        ]

        with patch("resource_pruner.time.sleep") as sleep:
            result = resource_pruner.wait_for_nat_gateways_deleted(ec2, ["nat-1"])

        self.assertTrue(result)
        sleep.assert_called_once_with(resource_pruner.NAT_DELETE_WAIT_DELAY)

    def test_release_eip_uses_current_unassociated_state(self):
        ec2 = Mock()
        ec2.describe_addresses.return_value = {
            "Addresses": [{"AllocationId": "eipalloc-1"}]
        }
        trace = {
            "resource_id": "eipalloc-1",
            "resource_type": "eip",
            "instance_type": "192.0.2.1",
            "state": "associated",
        }

        resource_pruner.release_eips(ec2, [trace], dry_run=False)

        ec2.disassociate_address.assert_not_called()
        ec2.release_address.assert_called_once_with(AllocationId="eipalloc-1")

    def test_release_eip_disassociates_current_regular_association(self):
        ec2 = Mock()
        ec2.describe_addresses.return_value = {
            "Addresses": [
                {
                    "AllocationId": "eipalloc-1",
                    "AssociationId": "eipassoc-1",
                }
            ]
        }
        trace = {
            "resource_id": "eipalloc-1",
            "resource_type": "eip",
            "state": "associated",
        }

        resource_pruner.release_eips(ec2, [trace], dry_run=False)

        ec2.disassociate_address.assert_called_once_with(
            AssociationId="eipassoc-1"
        )
        ec2.release_address.assert_called_once_with(AllocationId="eipalloc-1")


if __name__ == "__main__":
    unittest.main()
