"""Create and manage virtual machines with managed disks.

This script expects that the following environment vars are set:

AZURE_TENANT_ID: your Azure Active Directory tenant id or domain
AZURE_CLIENT_ID: your Azure Active Directory Application Client ID
AZURE_CLIENT_SECRET: your Azure Active Directory Application Secret
AZURE_SUBSCRIPTION_ID: your Azure Subscription Id
AZURE_RESOURCE_LOCATION: your resource location
ARM_ENDPOINT: your cloud's resource manager endpoint
"""
import os, json, random, traceback, uuid, logging
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import DiskCreateOption
from azure.identity import ClientSecretCredential

from msrestazure.azure_exceptions import CloudError

from haikunator import Haikunator
from msrestazure.azure_cloud import get_cloud_from_metadata_endpoint
from msrestazure.azure_active_directory import UserPassCredentials
from azure.profiles import KnownProfiles

haikunator = Haikunator()

# Resource Group
postfix = random.randint(100, 500)
GROUP_NAME = 'azure-sample-group-virtual-machines{}'.format(postfix)

# Network
VNET_NAME = 'azure-sample-vnet{}'.format(postfix)
SUBNET_NAME = 'azure-sample-subnet{}'.format(postfix)

# VM
OS_DISK_NAME = 'azure-sample-osdisk{}'.format(postfix)
STORAGE_ACCOUNT_NAME = haikunator.haikunate(delimiter='')

IP_CONFIG_NAME = 'azure-sample-ip-config{}'.format(postfix)
NIC_NAME = 'azure-sample-nic{}'.format(postfix)
USERNAME = 'userlogin'
PASSWORD = str(uuid.uuid4())
VM_NAME = 'VmName{}'.format(postfix)

VM_REFERENCE = {
    'linux': {
        'publisher': 'Canonical',
        'offer': 'UbuntuServer',
        'sku': '16.04-LTS',
        'version': 'latest'
    },
    'windows': {
        'publisher': 'MicrosoftWindowsServer',
        'offer': 'WindowsServer',
        'sku': '2016-Datacenter',
        'version': 'latest'
    }
}


def run_example(config):
    """Virtual Machine management example."""
    #
    # Create all clients with an Application (service principal) token provider
    #
    mystack_cloud = get_cloud_from_metadata_endpoint(
        config['resourceManagerUrl'])
    
    subscription_id = config['subscriptionId']
    # Azure Datacenter
    LOCATION = config['location']
    credentials = ClientSecretCredential(
        client_id = config['clientId'],
        client_secret = config['clientSecret'],
        tenant_id = config['tenantId'],
        authority = mystack_cloud.endpoints.active_directory)

    logging.basicConfig(level=logging.ERROR)
    scope = "openid profile offline_access" + " " + mystack_cloud.endpoints.active_directory_resource_id + "/.default"

    resource_client = ResourceManagementClient(
        credentials, subscription_id,
        base_url=mystack_cloud.endpoints.resource_manager,
        profile=KnownProfiles.v2020_09_01_hybrid,
        credential_scopes=[scope])

    compute_client = ComputeManagementClient(
        credentials,
        subscription_id,
        base_url=mystack_cloud.endpoints.resource_manager,
        profile=KnownProfiles.v2020_09_01_hybrid,
        credential_scopes=[scope])

    storage_client = StorageManagementClient(
        credentials,
        subscription_id,
        base_url=mystack_cloud.endpoints.resource_manager,
        profile=KnownProfiles.v2020_09_01_hybrid,
        credential_scopes=[scope])

    network_client = NetworkManagementClient(
        credentials,
        subscription_id,
        base_url=mystack_cloud.endpoints.resource_manager,
        profile=KnownProfiles.v2020_09_01_hybrid,
        credential_scopes=[scope])

    ###########
    # Prepare #
    ###########

    # Create Resource group
    print('\nCreate Resource Group')
    resource_client.resource_groups.create_or_update(GROUP_NAME, {'location': LOCATION})

    try:
        # Create a storage account
        print('\nCreate a storage account')
        storage_async_operation = storage_client.storage_accounts.begin_create(
            GROUP_NAME,
            STORAGE_ACCOUNT_NAME,
            {
                'sku': {'name': 'standard_lrs'},
                'kind': 'storage',
                'location': LOCATION
            }
        )
        storage_async_operation.result()

        # Create a NIC
        nic = create_nic(network_client, LOCATION)

        #############
        # VM Sample #
        #############

        # Create Linux VM
        print('\nCreating Linux Virtual Machine')
        vm_parameters = create_vm_parameters(nic.id, VM_REFERENCE['linux'], LOCATION)
        async_vm_creation = compute_client.virtual_machines.begin_create_or_update(
            GROUP_NAME,
            VM_NAME,
            vm_parameters)
        async_vm_creation.result()

        # Tag the VM
        print('\nTag Virtual Machine')
        async_vm_update = compute_client.virtual_machines.begin_create_or_update(
            GROUP_NAME,
            VM_NAME,
            {
                'location': LOCATION,
                'tags': {
                    'who-rocks': 'python',
                    'where': 'on azure'
                }
            }
        )
        async_vm_update.result()

        # Create managed data disk
        print('\nCreate (empty) managed Data Disk')
        async_disk_creation = compute_client.disks.begin_create_or_update(
            GROUP_NAME,
            'mydatadisk1',
            {
                'location': LOCATION,
                'disk_size_gb': 1,
                'creation_data': {
                    'create_option': DiskCreateOption.empty
                }
            }
        )
        data_disk = async_disk_creation.result()

        # Get the virtual machine by name
        print('\nGet Virtual Machine by Name')
        virtual_machine = compute_client.virtual_machines.get(
            GROUP_NAME,
            VM_NAME
        )

        # Attach data disk
        print('\nAttach Data Disk')
        virtual_machine.storage_profile.data_disks.append({
            'lun': 12,
            'name': 'mydatadisk1',
            'create_option': DiskCreateOption.attach,
            'managed_disk': {
                'id': data_disk.id
            }
        })
        async_disk_attach = compute_client.virtual_machines.begin_create_or_update(
            GROUP_NAME,
            virtual_machine.name,
            virtual_machine
        )
        async_disk_attach.result()

        # Detach data disk
        print('\nDetach Data Disk')
        data_disks = virtual_machine.storage_profile.data_disks
        data_disks[:] = [disk for disk in data_disks if disk.name != 'mydatadisk1']
        async_vm_update = compute_client.virtual_machines.begin_create_or_update(
            GROUP_NAME,
            VM_NAME,
            virtual_machine
        )
        virtual_machine = async_vm_update.result()

        # Deallocating the VM (in preparation for a disk resize)
        print('\nDeallocating the VM (to prepare for a disk resize)')
        async_vm_deallocate = compute_client.virtual_machines.begin_deallocate(
            GROUP_NAME, VM_NAME)
        async_vm_deallocate.result()

        # Increase OS disk size by 10 GB
        print('\nUpdate OS disk size')
        os_disk_name = virtual_machine.storage_profile.os_disk.name
        os_disk = compute_client.disks.get(GROUP_NAME, os_disk_name)
        if not os_disk.disk_size_gb:
            print(
                "\tServer is not returning the OS disk size, possible bug in the server?")
            print("\tAssuming that the OS disk size is 30 GB")
            os_disk.disk_size_gb = 30

        os_disk.disk_size_gb += 10

        async_disk_update = compute_client.disks.begin_create_or_update(
            GROUP_NAME,
            os_disk.name,
            os_disk
        )
        async_disk_update.result()

        # Start the VM
        print('\nStart VM')
        async_vm_start = compute_client.virtual_machines.begin_start(
            GROUP_NAME, VM_NAME)
        async_vm_start.result()

        # Restart the VM
        print('\nRestart VM')
        async_vm_restart = compute_client.virtual_machines.begin_restart(
            GROUP_NAME, VM_NAME)
        async_vm_restart.result()

        # Stop the VM
        print('\nStop VM')
        async_vm_stop = compute_client.virtual_machines.begin_power_off(
            GROUP_NAME, VM_NAME)
        async_vm_stop.result()

        # List VMs in subscription
        print('\nList VMs in subscription')
        for vm in compute_client.virtual_machines.list_all():
            print("\tVM: {}".format(vm.name))

        # List VM in resource group
        print('\nList VMs in resource group')
        for vm in compute_client.virtual_machines.list(GROUP_NAME):
            print("\tVM: {}".format(vm.name))

        # Delete VM
        print('\nDelete VM')
        async_vm_delete = compute_client.virtual_machines.begin_delete(
            GROUP_NAME, VM_NAME)
        async_vm_delete.result()

        # Create Windows VM
        print('\nCreating Windows Virtual Machine')
        # Recycling NIC of previous VM
        vm_parameters = create_vm_parameters(nic.id, VM_REFERENCE['windows'], LOCATION)
        async_vm_creation = compute_client.virtual_machines.begin_create_or_update(
            GROUP_NAME, VM_NAME, vm_parameters)
        async_vm_creation.result()
    except CloudError:
        print('A VM operation failed:', traceback.format_exc(), sep='\n')
    else:
        print('All example operations completed successfully!')
    finally:
        # Delete Resource group and everything in it
        print('\nDelete Resource Group')
        delete_async_operation = resource_client.resource_groups.begin_delete(
            GROUP_NAME)
        delete_async_operation.result()
        print("\nDeleted: {}".format(GROUP_NAME))


def create_nic(network_client, LOCATION):
    """Create a Network Interface for a VM.
    """
    # Create VNet
    print('\nCreate Vnet')
    async_vnet_creation = network_client.virtual_networks.begin_create_or_update(
        GROUP_NAME,
        VNET_NAME,
        {
            'location': LOCATION,
            'address_space': {
                'address_prefixes': ['10.0.0.0/16']
            }
        }
    )
    async_vnet_creation.result()

    # Create Subnet
    print('\nCreate Subnet')
    async_subnet_creation = network_client.subnets.begin_create_or_update(
        GROUP_NAME,
        VNET_NAME,
        SUBNET_NAME,
        {'address_prefix': '10.0.0.0/24'}
    )
    subnet_info = async_subnet_creation.result()

    # Create NIC
    print('\nCreate NIC')
    async_nic_creation = network_client.network_interfaces.begin_create_or_update(
        GROUP_NAME,
        NIC_NAME,
        {
            'location': LOCATION,
            'ip_configurations': [{
                'name': IP_CONFIG_NAME,
                'subnet': {
                    'id': subnet_info.id
                }
            }]
        }
    )
    return async_nic_creation.result()


def create_vm_parameters(nic_id, vm_reference, LOCATION):
    """Create the VM parameters structure.
    """
    return {
        'location': LOCATION,
        'os_profile': {
            'computer_name': VM_NAME,
            'admin_username': USERNAME,
            'admin_password': PASSWORD
        },
        'hardware_profile': {
            'vm_size': 'Standard_DS1_v2'
        },
        'storage_profile': {
            'image_reference': {
                'publisher': vm_reference['publisher'],
                'offer': vm_reference['offer'],
                'sku': vm_reference['sku'],
                'version': vm_reference['version']
            },
        },
        'network_profile': {
            'network_interfaces': [{
                'id': nic_id,
            }]
        },
    }


if __name__ == "__main__":
    with open('../azureAppSpConfig.json', 'r') as f:
        config = json.load(f)
    run_example(config)