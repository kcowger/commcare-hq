from uuid import uuid4

from casexml.apps.case.mock import CaseBlock
from dimagi.utils.couch import CriticalSection

from corehq.apps.celery import task
from corehq.apps.events.models import (
    ATTENDEE_USER_ID_CASE_PROPERTY,
    LOCATION_IDS_CASE_PROPERTY,
    AttendeeModel,
    get_attendee_case_type, PRIMARY_LOCATION_ID_CASE_PROPERTY,
)
from corehq.apps.hqcase.case_helper import CaseHelper
from corehq.apps.hqcase.utils import bulk_update_cases
from corehq.apps.users.models import CommCareUser


@task(queue='background_queue', ignore_result=True)
def sync_mobile_worker_attendees(domain_name, user_id):
    """
    Create attendees from mobile workers
    """
    with CriticalSection(['sync_mobile_worker_attendees_' + domain_name]):
        closed_cases = []
        user_id_model_mapping = get_user_attendee_models_on_domain(domain_name)
        attendee_case_type = get_attendee_case_type(domain_name)

        for user in CommCareUser.by_domain(domain_name):
            if user.user_id not in user_id_model_mapping:
                create_attendee_for_user(
                    user,
                    case_type=attendee_case_type,
                    domain=domain_name,
                    xform_user_id=user_id,
                    xform_device_id='corehq.apps.events.tasks.'
                                    'sync_mobile_worker_attendees',
                )
            elif user_id_model_mapping[user.user_id].case.closed:
                closed_cases.append(user_id_model_mapping[user.user_id].case)

        reopen_cases(closed_cases)


@task(queue='background_queue', ignore_result=True)
def close_mobile_worker_attendee_cases(domain_name):
    """
    Close attendee cases associated with mobile workers
    """
    with CriticalSection(['close_mobile_worker_attendees_' + domain_name]):
        user_id_model_mapping = get_user_attendee_models_on_domain(domain_name)
        user_ids = set(CommCareUser.ids_by_domain(domain_name))

        case_changes = [
            (model.case_id, {}, True)
            for user_id, model in user_id_model_mapping.items()
            if user_id in user_ids
        ]

        if case_changes:
            bulk_update_cases(
                domain_name,
                case_changes,
                device_id='corehq.apps.events.tasks.'
                          'close_mobile_worker_attendee_cases'
            )


def get_user_attendee_models_on_domain(domain):
    """
    Returns a mapping like ``{user_id1: model1, user_id2: model2}``.

    AttendeeModel.case is the attendee's CommCareCase.
    AttendeeModel.user_id is CommCareUser.user_id for attendees that are
    mobile workers. See AttendeeModel for other useful fields.
    """
    models = AttendeeModel.objects.by_domain(domain, include_closed=True)
    return {m.user_id: m for m in models if m.user_id}


def get_case_block_for_user(user, owner_id, attendee_case_type):
    case_name = user.username.split('@')[0]
    fields = {
        ATTENDEE_USER_ID_CASE_PROPERTY: user.user_id,
    }
    return CaseBlock(
        create=True,
        case_id=uuid4().hex,
        owner_id=owner_id,
        case_type=attendee_case_type,
        case_name=case_name,
        update=fields,
    )


def create_attendee_for_user(
    commcare_user,
    case_type,
    domain,
    xform_user_id,
    xform_device_id,
):
    helper = CaseHelper(domain=domain)
    helper.create_case(
        {
            'case_name': commcare_user.username.split('@')[0],
            'case_type': case_type,
            'properties': {
                ATTENDEE_USER_ID_CASE_PROPERTY: commcare_user.user_id,
                LOCATION_IDS_CASE_PROPERTY:
                    ' '.join(commcare_user.assigned_location_ids),
                PRIMARY_LOCATION_ID_CASE_PROPERTY:
                    commcare_user.location_id or '',
            }
        },
        user_id=xform_user_id,
        device_id=xform_device_id,
    )


def reopen_cases(cases):
    for case in cases:
        transactions = case.get_closing_transactions()
        for transaction in transactions:
            transaction.form.archive()