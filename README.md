# Cloud Agents

This is the source code for os-workloads, a prototype OpenStack Nova plugin to allow for inter-workload scaling request negotiations.

## Architecture

os-workloads is made up of a Nova v3 plugin, a Nova DB migration, and example agent scripts for Sahara and a generic image workload.

## Installation

Add the Nova extension to your install at nova/nova/api/openstack/compute/plugins/v3/workloads.py

For a devstack install, you can include this extension by modifying the /opt/stack/nova/nova.egg-info/entry_points.txt file and add a reference to it in the [nova.api.v3.extensions] section, such as:

workloads = nova.api.openstack.compute.plugins.v3.workloads:Workloads

Add the db migration to nova/nova/db/sqlalchemy/migrate_repo/versions/

Run:

nova-manage db sync

You'll now have an os-workloads REST endpoint.  Check out the plugin and agents for API usage examples.

