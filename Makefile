# The create-network argument to ncs-netsim
NETWORK = \
	create-network packages/cisco-iosxr-cli				4 cli-p \
	create-network packages/cisco-iosxr-netconf   4 netconf-p \

PACKAGES = cisco-iosxr-cli cisco-iosxr-netconf resource-manager sr-migrate

all: packages ncs-cdb netsim
.PHONY: all

packages:
	for i in $(PACKAGES); do \
	    $(MAKE) -C packages/$${i}/src all || exit 1; \
	done
.PHONY: packages

netsim:
	ncs-netsim --dir netsim $(NETWORK)
	for dir in netsim/*/*; do cp initial-data/$${dir##*/}.xml $${dir}/cdb; done
	ncs-netsim ncs-xml-init > ncs-cdb/netsim-devices-init.xml

ncs-cdb:
	ncs-setup --dest .

start:
	ncs-netsim start
	ncs
.PHONY: start

stop:
	ncs --stop
	ncs-netsim stop
.PHONY: sop

reset:
	ncs-netsim reset
	ncs-netsim start
	ncs-setup --reset
	ncs
.PHONY: reset

cli:
	ncs_cli -u admin
.PHONY: cli

clean:
	for i in $(PACKAGES); do \
	    $(MAKE) -C packages/$${i}/src clean || exit 1; \
	done
	rm -f README.ncs README.netsim ncs.conf
	rm -rf netsim running.DB logs state ncs-cdb *.trace
	rm -rf bin
.PHONY: clean
