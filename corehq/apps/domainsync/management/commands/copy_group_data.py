import logging
from couchdbkit import Database
from django.core.management.base import LabelCommand
from casexml.apps.case.models import CommCareCase
from corehq.apps.domain.models import Domain
from corehq.apps.groups.models import Group
from corehq.apps.users.models import CouchUser
from couchforms.models import XFormInstance
from dimagi.utils.chunked import chunked


class Command(LabelCommand):
    help = "Copy all data (users, forms, cases) associated with a single group"
    args = '<sourcedb> <group_id>'
    label = ""

    def handle(self, *args, **options):
        sourcedb = Database(args[0])
        group_id = args[1]

        print 'getting group'
        group = Group.wrap(sourcedb.get(group_id))
        group.save(force_update=True)

        print 'getting domain'
        domain = Domain.wrap(
            sourcedb.view('domain/domains', key=group.domain, include_docs=True,
                          reduce=False, limit=1).one()['doc']
        )
        domain.save(force_update=True)

        print 'getting cases'
        cases = sourcedb.view(
            'hqcase/by_owner',
            keys=[
                [group.domain, group_id, False],
                [group.domain, group_id, True],
            ],
            wrapper=lambda row: CommCareCase.wrap(row['doc']),
            reduce=False,
            include_docs=True
        ).all()
        CommCareCase.get_db().bulk_save(cases)

        print 'compiling xform_ids'
        xform_ids = set()
        for case in cases:
            xform_ids.update(case.xform_ids)

        print 'getting xforms'
        user_ids = set(group.users)
        CHUNK_SIZE = 100

        def form_wrapper(row):
            doc = row['doc']
            doc.pop('_attachments', None)
            return XFormInstance.wrap(doc)
        for i, subset in enumerate(chunked(xform_ids, CHUNK_SIZE)):
            print i * CHUNK_SIZE
            xforms = sourcedb.all_docs(
                keys=list(subset),
                include_docs=True,
                wrapper=form_wrapper,
            ).all()
            XFormInstance.get_db().bulk_save(xforms)

            for xform in xforms:
                user_id = xform.metadata.userID
                user_ids.add(user_id)

        print 'getting users'

        def wrap_user(row):
            doc = row['doc']
            try:
                return CouchUser.wrap_correctly(doc)
            except Exception as e:
                logging.exception('trouble with user %s' % doc['_id'])
            return None

        users = sourcedb.all_docs(
            keys=list(user_ids),
            include_docs=True,
            wrapper=wrap_user
        ).all()
        for user in users:
            # if we use bulk save, django user doesn't get sync'd
            if user:
                user.save()
