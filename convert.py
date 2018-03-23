
import argparse
import lxml.etree as et
import logging
import ovirtsdk4 as sdk


XML_NAMESPACES = {
    "ovf": "http://schemas.dmtf.org/ovf/envelope/1",
    "ovirt": "http://www.ovirt.org/ovf",
    "rasd": "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData",
    "vssd": "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xenovf": "http://schemas.citrix.com/ovf/envelope/1"
}


# Hardware section
# ---- common ----
# - disk
# - cd / dvd
# - network adapters
# - cpus
# - memory


def prefix_ns(ns, val):
    return "{%s}%s" % (XML_NAMESPACES[ns], val)


def handle_elem(elem, handlers, mapper=None):
    if mapper is None:
        mapper = lambda e: e.tag

    key = mapper(elem)
    if key not in handlers:
        logging.warn("Unknown tag, skipping: %s (%s)", key, elem.tag)
        return

    handlers[key](elem)


def noop_handler(elem):
    pass


def ignore_and_warn(elem):
    logging.warn("Ignoring element: %s", elem.tag)


class ResourceType(object):
    OTHER = 0

    CPU = 3
    MEMORY = 4

    ETHERNET = 10
    NET_OTHER = 11

    FLOPPY_DRIVE = 14
    CD_DRIVE = 15
    DVD_DRIVE = 16
    DISK_DRIVE = 17

    STORAGE_EXTENT = 19


class VM(object):
    def __init__(self):
        self.name = None
        self.cpu = None
        self.memory_bytes = None

    def build_from_xen_ovf(self, ovf_root):
        self._read_ovf_envelope(ovf_root)
        self._check_required_fields()
        self._fill_missing_fields()

    def add_vm_to_ovirt(self, conn):
        raise NotImplementedError

    def _read_ovf_envelope(self, elem):
        for e in elem:
            handle_elem(e, {
                prefix_ns("ovf", "References"): noop_handler,
                prefix_ns("ovf", "DiskSection"): noop_handler,
                prefix_ns("ovf", "NetworkSection"): noop_handler,
                prefix_ns("ovf", "StartupSection"): ignore_and_warn,
                prefix_ns("ovf", "VirtualSystem"): self._read_ovf_virtual_system
            })

    def _read_ovf_virtual_system(self, elem):
        def set_name(name_elem):
            self.name = name_elem.text

        for e in elem:
            handle_elem(e, {
                prefix_ns("ovf", "Info"): ignore_and_warn,
                prefix_ns("ovf", "Name"): set_name,
                prefix_ns("ovf", "OperatingSystemSection"): ignore_and_warn,
                prefix_ns("ovf", "VirtualHardwareSection"): self._read_hardware
            })

    def _read_hardware(self, elem):
        def handle_item(item):
            handle_elem(item, {
                ResourceType.CPU: self._read_hw_cpu,
                ResourceType.MEMORY: self._read_hw_memory,
                ResourceType.ETHERNET: ignore_and_warn,
                ResourceType.CD_DRIVE: ignore_and_warn,
                ResourceType.DVD_DRIVE: ignore_and_warn,
                ResourceType.STORAGE_EXTENT: ignore_and_warn
            }, lambda e: int(e.xpath("rasd:ResourceType/text()", namespaces=e.nsmap)[0]))

        def handle_other_config(elem):
            handle_elem(elem, {
                "HVM_boot_params": ignore_and_warn,
                "HVM_boot_policy": ignore_and_warn,
                "platform": ignore_and_warn,
                "hardware_platform_version": ignore_and_warn
            }, lambda e: e.attrib["Name"])

        for e in elem:
            handle_elem(e, {
                prefix_ns("ovf", "Info"): ignore_and_warn,
                prefix_ns("ovf", "System"): ignore_and_warn,
                prefix_ns("ovf", "Item"): handle_item,
                prefix_ns("xenovf", "VirtualSystemOtherConfigurationData"): handle_other_config
            })

    def _read_hw_cpu(self, elem):
        if self.cpu is not None:
            raise RuntimeError("OVF contains multiple CPU elements.")

        count = int(elem.xpath("rasd:VirtualQuantity/text()", namespaces=elem.nsmap)[0])

        # TODO - parse cpu-per-socket filed
        # Using CPU count as the number of sockets
        self.cpu = sdk.types.Cpu(
            topology=sdk.types.CpuTopology(
                sockets=count,
                cores=1,
                threads=1
            )
        )

    def _read_hw_memory(self, elem):
        if self.memory_bytes is not None:
            raise RuntimeError("OVF contains multiple memory elements.")

        # Check if allocation units are MB
        units = elem.xpath("rasd:AllocationUnits/text()", namespaces=elem.nsmap)[0]
        if units != 'byte * 2^20':
            raise RuntimeError("Memory units are not MB")

        mem_mb = int(elem.xpath("rasd:VirtualQuantity/text()", namespaces=elem.nsmap)[0])
        self.memory_bytes = mem_mb * 1024 * 1024

    def _check_required_fields(self):
        if self.name is None:
            raise RuntimeError("Name is missing!")

        if self.cpu is None:
            raise RuntimeError("CPU information is missing!")

        if self.memory_bytes is None:
            raise RuntimeError("Memory information is missing!")

    def _fill_missing_fields(self):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", help="URL of the oVirt engine API")
    parser.add_argument("--user", help="oVirt user name")
    parser.add_argument("--password", help="oVirt user password")
    parser.add_argument("--cluster", help="Name or ID of the cluster, where the VM will be created.")
    parser.add_argument("--domain", help="Name or ID of the storage domain, where the VM's disks be created")
    parser.add_argument("ovf_file", help="Xen OVF file")
    args = parser.parse_args()

    ovf_root = et.parse(args.ovf_file).getroot()

    vm = VM()
    vm.build_from_xen_ovf(ovf_root)

    connection = sdk.Connection(
        url=args.engine,
        username=args.user,
        password=args.password,
        insecure=True
    )

    connection.test(raise_exception=True)

    vm.add_vm_to_ovirt(connection)


if __name__ == '__main__':
    main()
