# Copyright 2015 Hewlett-Packard Development Company, L.P.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import itertools
import os

from oslo_config import cfg
from webob import exc

from nova.api.openstack import common
from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api import validation
from nova import exception
from nova import compute
from nova import objects
from nova.i18n import _
from nova import utils
from nova import quota

from oslo_config import cfg

from sqlalchemy import (Column, Index, Integer, BigInteger, Enum, String,
                        schema, Unicode, or_)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import orm, and_
from sqlalchemy import ForeignKey, DateTime, Boolean, Text, Float
from nova.db.sqlalchemy.models import NovaBase
from nova.db.sqlalchemy.api import model_query
from sqlalchemy.sql import func
from sqlalchemy.sql.expression import asc
from sqlalchemy.sql.expression import desc

from nova.db.sqlalchemy import types
from nova.db.sqlalchemy.models import Instance
from nova.compute import vm_states
from oslo_log import log as logging

LOG = logging.getLogger(__name__)
ALIAS = "os-workloads"
authorize = extensions.os_compute_authorizer(ALIAS)
CONF = cfg.CONF
BASE = declarative_base()
QUOTAS = quota.QUOTAS
ORDER_STATUSES = ["OPEN","FILLED","PENDING","ERROR","WORKING"]

class WorkloadsController(wsgi.Controller):

    def __init__(self, network_api=None):
        self.compute_api = compute.API(skip_policy_check=True)
        self.last_call = {}

    def workloads_get_all(self,context):
        return model_query(context, Workload).\
                   filter_by(project_id=context.project_id)

    @extensions.expected_errors(503)
    def index(self, req):
        """Return a list of all workloads."""
        context = req.environ['nova.context']
        authorize(context)
        workloads = []

        builds = self.workloads_get_all(context)

        for workload in builds:
            LOG.debug("Inspecting workload %s", workload.name)

            result = model_query(context, Instance, (
                func.count(Instance.id),
                func.sum(Instance.memory_mb))).\
                filter(or_(Instance.display_name.like(workload.name+"-%"),
                    Instance.display_name.like(workload.name+"\ -%"))).\
                filter(and_(
                    Instance.deleted != Instance.id,
                    Instance.vm_state != vm_states.SOFT_DELETED
                    )).\
                filter_by(project_id=context.project_id).first()

            instances = result[0]
            memory_mb = result[1] or 0
            workloads.append({
                           'id': workload.id,
                           'name': workload.name,
                           'priority': workload.priority,
                           'instances': instances,
                           'memory_mb': int(memory_mb)})
        #LOG.debug("Got result %s", str(workloads))

        return {'workloads': workloads}

    def create(self, req, body):
        """
        Create workload.
        """
        context = req.environ['nova.context']
        authorize(context, action='create')
        params = body['workload']
        workload = Workload()
        workload.name = params['name']
        workload.project_id = context.project_id
        workload.priority = int(params.get('priority') or 1)
        workload.save()
        return {'workload': workload}

    def update_pending_orders(self, context):

        orders = model_query(context, WorkloadOrder).\
                filter_by(status="PENDING").\
                join((Workload,
                Workload.id == WorkloadOrder.workload_id)).\
                filter_by(project_id = context.project_id).\
                order_by(asc(Workload.priority))
        orderlist = []
        for order in orders:
            orderlist.append(order['id'])

        # We seem to have to do this two-step because of SQLAlchemy
        # Session issues
        
        for order_id in orderlist:

            order = model_query(context, WorkloadOrder).\
                   filter_by(id=order_id).first()
            quotas = QUOTAS.get_project_quotas(context, context.project_id)
            ram_clear = instances_clear = False
            for entry in quotas:
                if entry == "ram":
                    ram_clear = (quotas[entry]['reserved']+quotas[entry]['in_use']+((order["memory_mb"] or 1024)*order['instances'])) <= quotas[entry]['limit']
                if entry == "instances":
                    instances_clear = (quotas[entry]['reserved']+quotas[entry]['in_use']+(order["instances"] or 1)) <= quotas[entry]['limit']
            if instances_clear and ram_clear:
                LOG.debug("Updating order status to open %s", (str(order['id'])))
                order.status = "OPEN"
                order.save()


    def show(self, req, id):
        context = req.environ['nova.context']
        authorize(context)

        # Check to see if we have workload orders that should be open
        self.update_pending_orders(context)

        query = model_query(context, Workload).\
                   filter_by(project_id=context.project_id).\
                   filter_by(id=int(id))
        workload = query.first()
        orders = []

        if workload:
            # Check and see if we're elidgible for scale-down.
            # We aren't if we already have open scale-down
            # orders.

            elidgible = True
            query = model_query(context, WorkloadOrder).\
                        filter(or_(
                        WorkloadOrder.status == "OPEN",
                        WorkloadOrder.status == "WORKING"
                        )).filter_by(workload_id=workload.id)

            for order in query:
                if order['instances'] < 0:
                    # Pre-existing scale down order.
                    elidgible = False

            if elidgible:
                # First check to see if there are pending orders for
                # higher priority workloads.

                query = model_query(context, WorkloadOrder).\
                        filter_by(status="PENDING").\
                        join((Workload,
                        Workload.id == WorkloadOrder.workload_id)).\
                        filter_by(project_id = context.project_id).\
                        filter(Workload.priority < workload.priority)

                for pending in query:
                    # We have a higher priority workload in a pending state, insert a scale-down order.
                    # This should probably check and ensure an existing scale-down order doesn't exist.
                    order = WorkloadOrder()
                    order.workload_id = workload.id
                    order.instances = pending['instances'] * -1
                    order.memory_mb = pending['memory_mb']
                    order.status = "OPEN"
                    order.save()
                    # Only handle one at a time.
                    break

            # # Check and see if we have pending orders that
            # # can now be opened.
            
            # query = model_query(context, WorkloadOrder).\
            #        filter_by(workload_id=workload.id).\
            #        filter_by(status="PENDING")

            # for order in query:
            #     quotas = QUOTAS.get_project_quotas(context, context.project_id)
            #     ram_clear = instances_clear = False
            #     for entry in quotas:
            #         if entry == "ram":
            #             ram_clear = (quotas[entry]['reserved']+quotas[entry]['in_use']+(order["memory_mb"] or 1024)) <= quotas[entry]['limit']
            #         if entry == "instances":
            #             instances_clear = (quotas[entry]['reserved']+quotas[entry]['in_use']+(order["instances"] or 1)) <= quotas[entry]['limit']
            #     if ram_clear and instances_clear:
            #         order.status = "OPEN"
            #         order.save()

            # Now list the orders we have open.

            query = model_query(context, WorkloadOrder).\
                   filter_by(workload_id=workload.id).\
                   filter_by(status="OPEN")
            for order in query:
                orders.append({"id":order.id,"instances":order.instances,"memory_mb":order.memory_mb})
        else:
            return {}
        return {"orders":orders}

    def update(self,req, id, body):
        """
        Update a workload. 

        Potential arguments:

        {"workload": {"name": "New Name", "priority": 5}}

        {"order": {"id": 1, "status": "FILLED"}}

        {"order": {"instances": 1, "memory_mb": 4096}}

        """
        context = req.environ['nova.context']
        authorize(context)
        workload = model_query(context, Workload).\
                   filter_by(project_id=context.project_id).\
                   filter_by(id=int(id)).first()
        orders = []
        if workload:
            if body.get("workload"):
                if body['workload'].get("name"):
                    workload.name = body['workload'].get("name")
                if body['workload'].get("priority"):
                    workload.priority = body['workload'].get("priority")
                workload.save()
            if body.get("order"):
                for order_req in body.get("order"):

                    if order_req.get("id"):
                        # We're updating an existing order.
                        order = model_query(context, WorkloadOrder).\
                            filter_by(workload_id=workload.id).\
                            filter_by(id=order_req['id']).first()

                        if order.status == "OPEN" or order.status == "PENDING":
                            if order_req.get("instances"):
                                order.instances = order_req.get("instances")
                            if order_req.get("memory_mb"):
                                order.memory_mb = order_req.get("memory_mb")

                        if order_req.get("status") and order_req.get("status") in ORDER_STATUSES:
                            order.status = order_req.get("status")
                        order.save()
                        orders.append(order)

                    elif order_req.get("instances") or order_req.get("memory_mb"):
                        
                        # We're creating a new order.

                        # At some point we should check if we have
                        # an existing open/pending order and just update that.

                        order_status = "OPEN"

                        # If it's a grow order, check and see if we're at capacity.

                        if (order_req.get("instances") or 1) > 0:
                            # Check to see if we have a pending order.
                            pending_order = model_query(context, WorkloadOrder).\
                                filter_by(workload_id=workload.id).\
                                filter(or_(WorkloadOrder.status=="PENDING",WorkloadOrder.status=="OPEN")).\
                                filter(WorkloadOrder.instances >= 1).first()
                            if pending_order:
                                return {"status":"Failure","message":"Existing pending or open order."}
                                
                            # We're growing, check to see if we can fit under quota limits.

                            quotas = QUOTAS.get_project_quotas(context, context.project_id)
                            instances = order_req.get("instances") or 1

                            for entry in quotas:
                                if entry == "ram":
                                    LOG.debug("Quota check %s", str(quotas[entry]['reserved']+quotas[entry]['in_use']+(order_req.get("memory_mb") or 1024)))
                                    if (quotas[entry]['reserved']+quotas[entry]['in_use']+((order_req.get("memory_mb") or 1024)*instances)) >= quotas[entry]['limit']:
                                        order_status = "PENDING"
                                if entry == "instances":
                                    if (quotas[entry]['reserved']+quotas[entry]['in_use']+(order_req.get("instances") or 1)) >= quotas[entry]['limit']:
                                        order_status = "PENDING"

                        order = WorkloadOrder()
                        order.workload_id = workload.id
                        order.instances = order_req.get("instances") or 1
                        order.memory_mb = order_req.get("memory_mb") or 0
                        order.status = order_status
                        order.save()
                        orders.append(order)

        return {"workload":workload,"order":orders}

    def delete(self, req, id):
        context = req.environ['nova.context']
        authorize(context)
        workload = model_query(context, Workload).\
                   filter_by(project_id=context.project_id).\
                   filter_by(id=int(id)).first()
        if workload:
            result = model_query(context, Workload).\
                    filter_by(project_id=context.project_id).\
                    filter_by(id=int(id)).\
                    soft_delete()
            return {"status":"SUCCESS"}
        else:
            return {"status":"FAILURE"}


class Workloads(extensions.V3APIExtensionBase):
    """API Workloads information."""

    name = "Workloads"
    alias = ALIAS
    version = 1

    def get_resources(self):
        resources = [
            extensions.ResourceExtension(ALIAS, WorkloadsController())
            ]
        return resources

    def get_controller_extensions(self):
        return []


class Workload(BASE, NovaBase):
    """Represents a Workload that can be made up of multiple VMs."""
    __tablename__ = 'workloads'
    id = Column(Integer, primary_key=True, autoincrement=True)
    deleted = Column(String(36), default="")

    name = Column(String(255))

    project_id = Column(String(255))

    priority = Column(Integer)
    last_checkin = Column(DateTime)

class WorkloadOrder(BASE, NovaBase):
    """Represents a Order related to a Workload."""
    __tablename__ = 'workload_orders'
    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(255))

    workload_id = Column(Integer)
    instances = Column(Integer)
    memory_mb = Column(Integer)
    workload = orm.relationship(Workload,
                                foreign_keys=workload_id,
                                primaryjoin='and_('
                                    'WorkloadOrder.workload_id == Workload.id,'
                                    'Workload.deleted == 0,'
                                    'WorkloadOrder.deleted == 0)')
