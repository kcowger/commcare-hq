from django import forms
from django.contrib.auth.models import User
import re
from corehq.apps.domain.forms import clean_password, max_pwd, _BaseForm
from django.core.validators import validate_email
from corehq.apps.domain.models import Domain
from corehq.apps.domain.utils import new_domain_re, new_org_re, new_org_title_re
from corehq.apps.registration.models import RegistrationRequest
from corehq.apps.orgs.models import Organization

class NewWebUserRegistrationForm(forms.Form):
    """
    Form for a brand new user, before they've created a domain or done anything on CommCare HQ.
    """
    full_name = forms.CharField(label='Full Name', max_length=User._meta.get_field('first_name').max_length+User._meta.get_field('last_name').max_length+1)
    email = forms.EmailField(label='Email Address',
                                    max_length=User._meta.get_field('email').max_length,
                                    help_text='You will use this email to log in.')
    password  =  forms.CharField(label='Password', max_length=max_pwd, widget=forms.PasswordInput(render_value=False))

    def clean_full_name(self):
        data = self.cleaned_data['full_name'].split()
        return [data.pop(0)] + [' '.join(data)]

    def clean_email(self):
        data = self.cleaned_data['email'].strip()
        validate_email(data)
        if User.objects.filter(username__iexact=data).count() > 0:
            raise forms.ValidationError('Username already taken; please try another')
        return data

    def clean_password(self):
        return clean_password(self.cleaned_data.get('password'))

    def clean(self):
        for field in self.cleaned_data:
            if isinstance(self.cleaned_data[field], basestring):
                self.cleaned_data[field] = self.cleaned_data[field].strip()
        return self.cleaned_data


class OrganizationRegistrationForm(forms.Form):
    """
    form for creating an organization for the first time
    """

    org_title = forms.CharField(label='Organization Title:', max_length=25)
    org_name = forms.CharField(label='Organization Name:', max_length=25)
    email = forms.CharField(label='Organization Email:', max_length=35)
    url = forms.CharField(label='Organization URL:', max_length=35)
    location = forms.CharField(label='Organization Location:', max_length=25)

    tos_confirmed = forms.BooleanField(required=False, label="Terms of Service") # Must be set to False to have the clean_*() routine called

    def clean_org_name(self):
        data = self.cleaned_data['org_name'].strip().lower()
        if not re.match("^%s$" % new_org_re, data):
            raise forms.ValidationError('Only lowercase letters and numbers allowed. Single hyphens may be used to separate words.')
        if Organization.get_by_name(data) or Organization.get_by_name(data.replace('-', '.')):
            raise forms.ValidationError('Organization name already taken---please try another')
        return data

    def clean_org_title(self):
        data = self.cleaned_data['org_title'].strip()
        if not re.match("^%s$" % new_org_title_re, data):
            raise forms.ValidationError('Only letters and numbers allowed. Single hyphens may be used to separate words.')
        return data

    def clean_email(self):
        data = self.cleaned_data['email'].strip()
        if not re.match("^[a-zA-Z0-9._%-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,4}$", data):
            raise forms.ValidationError('invalid email address')
        return data



    def clean_url(self):
        data = self.cleaned_data['url'].strip()
        if not re.match("^(http(s?)\:\/\/|~/|/)?([a-zA-Z]{1}([\w\-]+\.)+([\w]{2,5}))(:[\d]{1,5})?/?(\w+\.[\w]{3,4})?((\?\w+=\w+)?(&\w+=\w+)*)?", data):
            raise forms.ValidationError('invalid url')
        return data

    def clean_location(self):
        data = self.cleaned_data['location']
        return data

    def clean_tos_confirmed(self):
        data = self.cleaned_data['tos_confirmed']
        if data != True:
            raise forms.ValidationError('You must agree to our Terms Of Service in order to create your own project.')
        return data

    def clean(self):
        for field in self.cleaned_data:
            if isinstance(self.cleaned_data[field], basestring):
                self.cleaned_data[field] = self.cleaned_data[field].strip()
        return self.cleaned_data

class DomainRegistrationForm(forms.Form):
    """
    Form for creating a domain for the first time
    """
    domain_name =  forms.CharField(label='Project Name:', max_length=25)
    tos_confirmed = forms.BooleanField(required=False, label="Terms of Service") # Must be set to False to have the clean_*() routine called

    def clean_domain_name(self):
        data = self.cleaned_data['domain_name'].strip().lower()
        if not re.match("^%s$" % new_domain_re, data):
            raise forms.ValidationError('Only lowercase letters and numbers allowed. Single hyphens may be used to separate words.')
        if Domain.get_by_name(data) or Domain.get_by_name(data.replace('-', '.')):
            raise forms.ValidationError('Project name already taken---please try another')
        return data

    def clean_tos_confirmed(self):
        data = self.cleaned_data['tos_confirmed']
        if data != True:
            raise forms.ValidationError('You must agree to our Terms Of Service in order to create your own project.')
        return data

    def clean(self):
        for field in self.cleaned_data:
            if isinstance(self.cleaned_data[field], basestring):
                self.cleaned_data[field] = self.cleaned_data[field].strip()
        return self.cleaned_data