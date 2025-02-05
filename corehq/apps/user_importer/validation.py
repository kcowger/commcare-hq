from abc import ABCMeta, abstractmethod
from collections import Counter

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext as _

from corehq.apps.user_importer.helpers import spec_value_to_boolean_or_none
from corehq.apps.users.dbaccessors import get_existing_usernames
from dimagi.utils.chunked import chunked
from dimagi.utils.parsing import string_to_boolean

from corehq.apps.domain.forms import clean_password
from corehq.apps.enterprise.models import EnterprisePermissions
from corehq.apps.reports.models import TableauUser
from corehq.apps.reports.util import get_allowed_tableau_groups_for_domain
from corehq.apps.locations.models import SQLLocation
from corehq.apps.locations.permissions import user_can_access_other_user, user_can_change_locations
from corehq.apps.user_importer.exceptions import UserUploadError
from corehq.apps.users.forms import get_mobile_worker_max_username_length
from corehq.apps.users.models import CouchUser, Invitation
from corehq.apps.users.util import normalize_username, raw_username
from corehq.apps.users.views.utils import (
    user_can_access_invite
)
from corehq.util.workbook_json.excel import (
    StringTypeRequiredError,
    enforce_string_type,
)


def get_user_import_validators(domain_obj, all_specs, is_web_user_import, allowed_groups=None, allowed_roles=None,
                               profiles_by_name=None, upload_domain=None, upload_user=None, location_cache=None):
    domain = domain_obj.name
    validate_passwords = domain_obj.strong_mobile_passwords
    noop = NoopValidator(domain)

    validators = [
        UsernameTypeValidator(domain),
        DuplicateValidator(domain, 'username', all_specs),
        UsernameLengthValidator(domain),
        CustomDataValidator(domain, profiles_by_name),
        EmailValidator(domain, 'email'),
        RoleValidator(domain, allowed_roles),
        ExistingUserValidator(domain, all_specs),
        TargetDomainValidator(upload_domain),
        ProfileValidator(domain, list(profiles_by_name)),
        LocationAccessValidator(domain, upload_user, location_cache, is_web_user_import)
    ]
    if is_web_user_import:
        return validators + [RequiredWebFieldsValidator(domain), DuplicateValidator(domain, 'email', all_specs),
                             EmailValidator(domain, 'username'), TableauRoleValidator(domain),
                             TableauGroupsValidator(domain, all_specs)]
    else:
        return validators + [
            UsernameValidator(domain),
            BooleanColumnValidator(domain, 'is_active'),
            BooleanColumnValidator(domain, 'is_account_confirmed'),
            BooleanColumnValidator(domain, 'send_confirmation_email'),
            RequiredFieldsValidator(domain),
            DuplicateValidator(domain, 'user_id', all_specs),
            DuplicateValidator(domain, 'password', all_specs, is_password) if validate_passwords else noop,
            NewUserPasswordValidator(domain),
            PasswordValidator(domain) if validate_passwords else noop,
            GroupValidator(domain, allowed_groups),
            ConfirmationSmsValidator(domain)
        ]


class ImportValidator(metaclass=ABCMeta):
    error_message = None

    def __init__(self, domain):
        self.domain = domain

    def __call__(self, spec):
        error_message = self.validate_spec(spec)
        if error_message:
            raise UserUploadError(error_message)

    @abstractmethod
    def validate_spec(self, spec):
        raise NotImplementedError


class NoopValidator(ImportValidator):
    def validate_spec(self, spec):
        pass


class UsernameValidator(ImportValidator):
    error_message = _('username cannot contain spaces or symbols')

    def validate_spec(self, spec):
        username = spec.get('username')
        if username:
            try:
                normalize_username(str(username), self.domain)
            except TypeError:
                pass
            except ValidationError:
                return self.error_message


class BooleanColumnValidator(ImportValidator):
    _error_message = _("'{column_id}' column can only contain 'true' or 'false'")

    def __init__(self, domain, column_id):
        self.column_id = column_id
        super().__init__(domain)

    def validate_spec(self, spec):
        value = spec.get(self.column_id)
        if isinstance(value, str):
            try:
                string_to_boolean(value) if value else None
            except ValueError:
                return self.error_message

    @property
    def error_message(self):
        return self._error_message.format(column_id=self.column_id)


class RequiredFieldsValidator(ImportValidator):
    error_message = _("One of 'username' or 'user_id' is required")

    def validate_spec(self, spec):
        user_id = spec.get('user_id')
        username = spec.get('username')
        if not user_id and not username:
            return self.error_message


class RequiredWebFieldsValidator(ImportValidator):
    error_message = _("Upload of web users requires 'username' and 'role' for each user")

    def validate_spec(self, spec):
        username = spec.get('username')
        role = spec.get('role')
        if not username or not role:
            return self.error_message


class TableauRoleValidator(ImportValidator):
    _error_message = _("Invalid tableau role: '{}'. Please choose one of the following: {}")

    def __init__(self, domain):
        super().__init__(domain)
        self.valid_role_options = [e.value for e in TableauUser.Roles]

    def validate_spec(self, spec):
        tableau_role = spec.get('tableau_role')
        if tableau_role is not None and tableau_role not in self.valid_role_options:
            return self._error_message.format(tableau_role, ', '.join(self.valid_role_options))


class TableauGroupsValidator(ImportValidator):
    _error_message = _("These groups, {}, are not valid for this domain. Please choose from the following: {}")

    def __init__(self, domain, all_specs):
        super().__init__(domain)
        self.allowed_groups_for_domain = []
        if 'tableau_groups' in all_specs[0]:
            self.allowed_groups_for_domain = get_allowed_tableau_groups_for_domain(self.domain) or []

    def validate_spec(self, spec):
        tableau_groups = spec.get('tableau_groups') or []
        if tableau_groups:
            tableau_groups = tableau_groups.split(',')
        invalid_groups = []
        for group in tableau_groups:
            if group not in self.allowed_groups_for_domain:
                invalid_groups.append(group)
        if invalid_groups:
            return self._error_message.format(', '.join(invalid_groups), ', '.join(self.allowed_groups_for_domain))


class DuplicateValidator(ImportValidator):
    _error_message = _("'{field}' values must be unique")

    def __init__(self, domain, field, all_specs, check_function=None):
        super().__init__(domain)
        self.field = field
        self.check_function = check_function
        self.duplicates = find_duplicates(all_specs, field)

    @property
    def error_message(self):
        return self._error_message.format(field=self.field)

    def validate_spec(self, row_spec):
        item = row_spec.get(self.field)
        if not item:
            return

        if self.check_function and not self.check_function(item):
            return

        if item in self.duplicates:
            return self.error_message


def find_duplicates(specs, field):
    counter = Counter([
        spec.get(field) for spec in specs
    ])
    return {
        value for value, count in counter.items() if count > 1
    }


class UsernameLengthValidator(ImportValidator):
    _error_message = _("username cannot contain greater than {length} characters")

    def __init__(self, domain, max_length=None):
        super().__init__(domain)
        self.max_username_length = max_length or get_mobile_worker_max_username_length(self.domain)

    @property
    def error_message(self):
        return self._error_message.format(length=self.max_username_length)

    def validate_spec(self, spec):
        username = spec.get('username')
        if username:
            username = str(username)
        if len(raw_username(username)) > self.max_username_length:
            return self.error_message


class UsernameTypeValidator(ImportValidator):
    error_message = _("Username must be Text")

    def validate_spec(self, spec):
        username = spec.get('username')
        if not username:
            return
        try:
            enforce_string_type(username)
        except StringTypeRequiredError:
            return self.error_message


class NewUserPasswordValidator(ImportValidator):
    error_message = _("New users must have a password set.")

    def validate_spec(self, spec):
        user_id = spec.get('user_id')
        password = spec.get('password')
        is_account_confirmed = spec_value_to_boolean_or_none(spec, 'is_account_confirmed')
        web_user = spec.get('web_user')

        # explicitly check is_account_confirmed against False because None is the default
        if not user_id and not is_password(password) and is_account_confirmed is not False and not web_user:
            return self.error_message


class PasswordValidator(ImportValidator):
    def validate_spec(self, spec):
        password = spec.get('password')
        if is_password(password):
            try:
                clean_password(password)
            except ValidationError as e:
                return e.message


class CustomDataValidator(ImportValidator):
    def __init__(self, domain, profiles_by_name):
        super().__init__(domain)
        from corehq.apps.users.views.mobile.custom_data_fields import UserFieldsView
        self.custom_data_validator = UserFieldsView.get_validator(domain)
        self.profiles_by_name = profiles_by_name

    def validate_spec(self, spec):
        data = spec.get('data')
        profile_name = spec.get('user_profile')
        if data:
            if profile_name and self.profiles_by_name:
                profile = self.profiles_by_name.get(profile_name)
            else:
                profile = None
            return self.custom_data_validator(data, profile=profile)


class EmailValidator(ImportValidator):
    error_message = _("User has an invalid email address for their {}")

    def __init__(self, domain, column_id):
        super().__init__(domain)
        self.column_id = column_id

    def validate_spec(self, spec):
        email = spec.get(self.column_id)
        if email:
            try:
                validate_email(email)
            except ValidationError:
                return self.error_message.format(self.column_id)


class RoleValidator(ImportValidator):
    error_message = _("Role '{}' does not exist")

    def __init__(self, domain, allowed_roles=None):
        super().__init__(domain)
        self.allowed_roles = allowed_roles

    def validate_spec(self, spec):
        role = spec.get('role')
        if role and role not in self.allowed_roles:
            return self.error_message.format(role)


class ProfileValidator(ImportValidator):
    error_message = _("Profile '{}' does not exist")

    def __init__(self, domain, allowed_profiles=None):
        super().__init__(domain)
        self.allowed_profiles = allowed_profiles

    def validate_spec(self, spec):
        profile = spec.get('user_profile')
        if profile and profile not in self.allowed_profiles:
            return self.error_message.format(profile)


class GroupValidator(ImportValidator):
    error_message = _("Group '{}' does not exist (try adding it to your spreadsheet)")

    def __init__(self, domain, allowed_groups=None):
        super().__init__(domain)
        self.allowed_groups = allowed_groups

    def validate_spec(self, spec):
        group_names = list(map(str, spec.get('group') or []))
        for group_name in group_names:
            if group_name not in self.allowed_groups:
                return self.error_message.format(group_name)


def is_password(password):
    if not password:
        return False
    for c in str(password):
        if c != "*":
            return True
    return False


class ExistingUserValidator(ImportValidator):
    error_message = _("The username already belongs to a user. Specify an ID to update the user.")

    def __init__(self, domain, all_sepcs):
        super().__init__(domain)
        self.all_specs = all_sepcs
        self.existing_usernames = self.get_exising_users()

    def get_exising_users(self):
        usernames_without_ids = set()

        for row in self.all_specs:
            username = row.get('username')
            if row.get('user_id') or not username:
                continue

            try:
                usernames_without_ids.add(normalize_username(username, self.domain))
            except ValidationError:
                pass

        existing_usernames = set()
        for usernames in chunked(usernames_without_ids, 500):
            existing_usernames.update(get_existing_usernames(usernames))

        return existing_usernames

    def validate_spec(self, spec):
        try:
            username = normalize_username(spec.get('username'), self.domain)
        except ValidationError:
            return

        if username in self.existing_usernames:
            return self.error_message


class TargetDomainValidator(ImportValidator):
    error_message = _("Target domain {} does not use enterprise permissions of {}")

    def validate_spec(self, spec):
        target_domain = spec.get('domain')
        if target_domain and target_domain != self.domain:
            if target_domain not in EnterprisePermissions.get_domains(self.domain):
                return self.error_message.format(target_domain, self.domain)


class ConfirmationSmsValidator(ImportValidator):
    confirmation_sms_header = "send_confirmation_sms"
    account_confirmed_header = "is_account_confirmed"
    active_status_header = "is_active"
    error_new_user = _("When '{}' is True for a new user, {} must be either empty or set to False.")
    error_existing_user = _("When '{}' is True for an existing user, {}.")

    def validate_spec(self, spec):
        send_account_confirmation_sms = spec_value_to_boolean_or_none(spec, self.confirmation_sms_header)

        if send_account_confirmation_sms:
            is_active = spec_value_to_boolean_or_none(spec, self.active_status_header)
            is_account_confirmed = spec_value_to_boolean_or_none(spec, self.account_confirmed_header)
            user_id = spec.get('user_id')
            error_values = []
            if not user_id:
                if is_active:
                    error_values.append(self.active_status_header)
                if is_account_confirmed:
                    error_values.append(self.account_confirmed_header)
                if error_values:
                    return self.error_new_user.format(self.confirmation_sms_header, ' and '.join(error_values))
            else:
                if is_active:
                    error_values.append(f"{self.active_status_header} must be empty or set to False")
                if is_account_confirmed is not None:
                    error_values.append(f"{self.account_confirmed_header} must be empty")
                if error_values:
                    errors_formatted = ' and '.join(error_values)
                    return self.error_existing_user.format(self.confirmation_sms_header, errors_formatted)


class LocationAccessValidator(ImportValidator):
    error_message_user_access = _("Based on your locations do not have permission to edit this user or user "
                                  "invitation")
    error_message_location_access = _("You do not have permission to assign or remove these locations: {}")

    def __init__(self, domain, upload_user, location_cache, is_web_user_import):
        super().__init__(domain)
        self.upload_user = upload_user
        self.location_cache = location_cache
        self.is_web_user_import = is_web_user_import

    def validate_spec(self, spec):
        from corehq.apps.user_importer.importer import find_location_id
        # 1. Get current locations for user or user invitation and ensure user can edit it
        username = spec.get('username')
        current_locs = []
        editable_user = None
        if self.is_web_user_import:
            try:
                invitation = Invitation.objects.get(domain=self.domain, email=username, is_accepted=False)
                if not user_can_access_invite(self.domain, self.upload_user, invitation):
                    return self.error_message_user_access.format(invitation.email)
                current_locs = invitation.assigned_locations.all()
            except Invitation.DoesNotExist:
                editable_user = CouchUser.get_by_username(username, strict=True)
        else:
            if username:
                editable_user = CouchUser.get_by_username(username, strict=True)
            elif 'user_id' in spec:
                editable_user = CouchUser.get_by_user_id(spec.get('user_id'))
        if editable_user:
            if not user_can_access_other_user(self.domain, self.upload_user, editable_user):
                return self.error_message_user_access.format(editable_user.username)
            current_locs = editable_user.get_location_ids(self.domain)

        # 2. Ensure the user is only adding the user to/removing from *new locations* that they have permission
        # to access.
        if 'location_code' in spec:
            location_codes = (spec['location_code'] if isinstance(spec['location_code'], list)
                              else [spec['location_code']])
            locs_being_assigned = find_location_id(location_codes, self.location_cache)
            problem_location_ids = user_can_change_locations(self.domain, self.upload_user,
                                                            current_locs, locs_being_assigned)
            if problem_location_ids:
                return self.error_message_location_access.format(
                    ', '.join(SQLLocation.objects.filter(
                        location_id__in=problem_location_ids).values_list('site_code', flat=True)))
