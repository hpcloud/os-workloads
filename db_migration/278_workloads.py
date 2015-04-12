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

import sqlalchemy as sa

def upgrade(migrate_engine):
    meta = sa.MetaData(bind=migrate_engine)

    workloads = sa.Table('workloads', meta,
        sa.Column('created_at', sa.DateTime),
        sa.Column('updated_at', sa.DateTime),
        sa.Column('deleted_at', sa.DateTime),
        sa.Column('last_checkin', sa.DateTime),
        sa.Column('deleted', sa.String(length=255)),
        sa.Column('id', sa.Integer, primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=255), primary_key=True),
        sa.Column('name', sa.String(length=255)),
        sa.Column('priority', sa.Integer),
        mysql_engine='InnoDB',
        mysql_charset='utf8'
        )
    workloads.create()

    workloadorders = sa.Table('workload_orders', meta,
        sa.Column('created_at', sa.DateTime),
        sa.Column('updated_at', sa.DateTime),
        sa.Column('id', sa.Integer, primary_key=True, nullable=False),
        sa.Column('workload_id', sa.Integer),
        sa.Column('instances', sa.Integer),
        sa.Column('memory_mb', sa.Integer),
        sa.Column('status', sa.String(length=255)),
        sa.Column('deleted_at', sa.DateTime),
        sa.Column('deleted', sa.String(length=255)),
        mysql_engine='InnoDB',
        mysql_charset='utf8'
        )
    workloadorders.create()


def downgrade(migrate_engine):
    meta = sa.MetaData()
    meta.bind = migrate_engine
    table = sa.Table('workloads', meta, autoload=True)
    table.drop()
    table = sa.Table('workload_orders', meta, autoload=True)
    table.drop()
