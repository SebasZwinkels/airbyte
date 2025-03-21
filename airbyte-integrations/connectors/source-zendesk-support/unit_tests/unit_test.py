#
# Copyright (c) 2023 Airbyte, Inc., all rights reserved.
#


import calendar
import copy
import logging
import re
from datetime import datetime
from unittest.mock import Mock, patch

import pendulum
import pytest
import pytz
import requests
from source_zendesk_support.source import BasicApiTokenAuthenticator, SourceZendeskSupport
from source_zendesk_support.streams import (
    DATETIME_FORMAT,
    END_OF_STREAM_KEY,
    LAST_END_TIME_KEY,
    AccountAttributes,
    AttributeDefinitions,
    AuditLogs,
    BaseZendeskSupportStream,
    Brands,
    Categories,
    CustomRoles,
    GroupMemberships,
    Groups,
    Macros,
    OrganizationMemberships,
    Organizations,
    SatisfactionRatings,
    Schedules,
    Sections,
    SlaPolicies,
    SourceZendeskIncrementalExportStream,
    Tags,
    TicketAudits,
    TicketComments,
    TicketFields,
    TicketForms,
    TicketMetricEvents,
    TicketSkips,
    Topics,
    UserFields,
    UserSettingsStream,
)
from test_data.data import TICKET_EVENTS_STREAM_RESPONSE
from utils import read_full_refresh

from airbyte_cdk.models import SyncMode
from airbyte_cdk.sources.streams.http.error_handlers import ResponseAction


# prepared config
STREAM_ARGS = {
    "subdomain": "sandbox",
    "start_date": "2021-06-01T00:00:00Z",
    "authenticator": BasicApiTokenAuthenticator("test@airbyte.io", "api_token"),
}

# raw config
TEST_CONFIG = {
    "subdomain": "sandbox",
    "start_date": "2021-06-01T00:00:00Z",
    "credentials": {"credentials": "api_token", "email": "integration-test@airbyte.io", "api_token": "api_token"},
}

TEST_CONFIG_WITHOUT_START_DATE = {
    "subdomain": "sandbox",
    "credentials": {"credentials": "api_token", "email": "integration-test@airbyte.io", "api_token": "api_token"},
}


# raw config oauth
TEST_CONFIG_OAUTH = {
    "subdomain": "sandbox",
    "start_date": "2021-06-01T00:00:00Z",
    "credentials": {"credentials": "oauth2.0", "access_token": "test_access_token"},
}

DATETIME_STR = "2021-07-22T06:55:55Z"
DATETIME_FROM_STR = datetime.strptime(DATETIME_STR, DATETIME_FORMAT)
STREAM_URL = "https://subdomain.zendesk.com/api/v2/stream.json?&start_time=1647532987&page=1"
URL_BASE = "https://sandbox.zendesk.com/api/v2/"


def snake_case(name):
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def get_stream_instance(stream_class, args):
    return stream_class(**args)


def test_date_time_format():
    assert DATETIME_FORMAT == "%Y-%m-%dT%H:%M:%SZ"


def test_last_end_time_key():
    assert LAST_END_TIME_KEY == "_last_end_time"


def test_end_of_stream_key():
    assert END_OF_STREAM_KEY == "end_of_stream"


def test_token_authenticator():
    # we expect base64 from creds input
    expected = "dGVzdEBhaXJieXRlLmlvL3Rva2VuOmFwaV90b2tlbg=="
    result = BasicApiTokenAuthenticator("test@airbyte.io", "api_token")
    assert result._token == expected


@pytest.mark.parametrize(
    "config, expected",
    [
        (TEST_CONFIG, "aW50ZWdyYXRpb24tdGVzdEBhaXJieXRlLmlvL3Rva2VuOmFwaV90b2tlbg=="),
        (TEST_CONFIG_OAUTH, "test_access_token"),
    ],
    ids=["api_token", "oauth"],
)
def test_get_authenticator(config, expected):
    # we expect base64 from creds input
    result = SourceZendeskSupport(config=config, catalog=None, state=None).get_authenticator(config=config)
    assert result._token == expected


@pytest.mark.parametrize(
    "response, start_date, check_passed",
    [([{"active_features": {"organization_access_enabled": True}}], "2020-01-01T00:00:00Z", True), ([], "2020-01-01T00:00:00Z", False)],
    ids=["check_successful", "invalid_start_date"],
)
def test_check(response, start_date, check_passed):
    config = copy.deepcopy(TEST_CONFIG)
    config["start_date"] = start_date
    with patch.object(UserSettingsStream, "read_records", return_value=response) as mock_method:
        ok, _ = SourceZendeskSupport(config=config, catalog=None, state=None).check_connection(logger=logging.Logger, config=config)
        assert check_passed == ok
        if ok:
            mock_method.assert_called()


@pytest.mark.parametrize(
    "ticket_forms_response, status_code, expected_n_streams, expected_warnings, reason",
    [
        ('{"ticket_forms": [{"id": 1, "updated_at": "2021-07-08T00:05:45Z"}]}', 200, 40, [], None),
        (
            '{"error": "Not sufficient permissions"}',
            403,
            37,
            [
                "An exception occurred while trying to access TicketForms stream: Forbidden. You don't have permission to access this resource.. Skipping this stream."
            ],
            None,
        ),
        (
            "",
            404,
            37,
            [
                "An exception occurred while trying to access TicketForms stream: Not found. The requested resource was not found on the server.. Skipping this stream."
            ],
            "Not Found",
        ),
    ],
    ids=["forms_accessible", "forms_inaccessible", "forms_not_exists"],
)
def test_full_access_streams(caplog, requests_mock, ticket_forms_response, status_code, expected_n_streams, expected_warnings, reason):
    requests_mock.get("/api/v2/ticket_forms", status_code=status_code, text=ticket_forms_response, reason=reason)
    result = SourceZendeskSupport(config=TEST_CONFIG, catalog=None, state=None).streams(config=TEST_CONFIG)
    assert len(result) == expected_n_streams
    logged_warnings = (record for record in caplog.records if record.levelname == "WARNING")
    for msg in expected_warnings:
        assert msg in next(logged_warnings).message


@pytest.fixture(autouse=True)
def time_sleep_mock(mocker):
    time_mock = mocker.patch("time.sleep", lambda x: None)
    yield time_mock


def test_str_to_datetime():
    expected = datetime.strptime(DATETIME_STR, DATETIME_FORMAT)
    output = BaseZendeskSupportStream.str_to_datetime(DATETIME_STR)
    assert output == expected


def test_datetime_to_str():
    expected = datetime.strftime(DATETIME_FROM_STR.replace(tzinfo=pytz.UTC), DATETIME_FORMAT)
    output = BaseZendeskSupportStream.datetime_to_str(DATETIME_FROM_STR)
    assert output == expected


def test_str_to_unixtime():
    expected = calendar.timegm(DATETIME_FROM_STR.utctimetuple())
    output = BaseZendeskSupportStream.str_to_unixtime(DATETIME_STR)
    assert output == expected


def test_check_start_time_param():
    expected = 1626936955
    start_time = calendar.timegm(pendulum.parse(DATETIME_STR).utctimetuple())
    output = SourceZendeskIncrementalExportStream.validate_start_time(start_time)
    assert output == expected


def test_parse_response_from_empty_json(requests_mock):
    requests_mock.get(STREAM_URL, text="", status_code=403)
    test_response = requests.get(STREAM_URL)
    output = Schedules(**STREAM_ARGS).parse_response(test_response, {})
    assert list(output) == []


def test_parse_response(requests_mock):
    requests_mock.get(STREAM_URL, json=TICKET_EVENTS_STREAM_RESPONSE)
    test_response = requests.get(STREAM_URL)
    output = TicketComments(**STREAM_ARGS).parse_response(test_response)
    # get the first parsed element from generator
    parsed_output = list(output)[0]
    # check, if we have all transformations correctly
    for entity in TicketComments.list_entities_from_event:
        assert True if entity in parsed_output else False


class TestAllStreams:
    def test_ticket_forms_exception_stream(self):
        with patch.object(TicketForms, "read_records", return_value=[{}]) as mocked_records:
            mocked_records.side_effect = Exception("The error")
            streams = SourceZendeskSupport(config=TEST_CONFIG, catalog=None, state=None).streams(TEST_CONFIG)
            assert not any([isinstance(stream, TicketForms) for stream in streams])

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (AuditLogs, "audit_logs"),
            (GroupMemberships, "group_memberships"),
            (Groups, "groups"),
            (Macros, "macros"),
            (Organizations, "incremental/organizations.json"),
            (OrganizationMemberships, "organization_memberships"),
            (SatisfactionRatings, "satisfaction_ratings"),
            (SlaPolicies, "slas/policies.json"),
            (Tags, "tags"),
            (TicketAudits, "ticket_audits"),
            (TicketComments, "incremental/ticket_events.json"),
            (TicketFields, "ticket_fields"),
            (TicketForms, "ticket_forms"),
            (TicketSkips, "skips.json"),
            (TicketMetricEvents, "incremental/ticket_metric_events"),
            (Topics, "community/topics"),
            (Brands, "brands"),
            (CustomRoles, "custom_roles"),
            (Schedules, "business_hours/schedules.json"),
            (AccountAttributes, "routing/attributes"),
            (AttributeDefinitions, "routing/attributes/definitions"),
            (UserFields, "user_fields"),
            (Categories, "categories"),
            (Sections, "sections"),
        ],
        ids=[
            "AuditLogs",
            "GroupMemberships",
            "Groups",
            "Macros",
            "Organizations",
            "OrganizationMemberships",
            "SatisfactionRatings",
            "SlaPolicies",
            "Tags",
            "TicketAudits",
            "TicketComments",
            "TicketFields",
            "TicketForms",
            "TicketSkips",
            "TicketMetricEvents",
            "Topics",
            "Brands",
            "CustomRoles",
            "Schedules",
            "AccountAttributes",
            "AttributeDefinitions",
            "UserFields",
            "Categories",
            "Sections",
        ],
    )
    def test_path(self, stream_cls, expected):
        stream = get_stream_instance(stream_cls, STREAM_ARGS)
        result = stream.path(stream_slice={"ticket_id": "13"})
        assert result == expected


class TestSourceZendeskSupportStream:
    @pytest.mark.parametrize(
        "stream_cls",
        [(Macros), (Groups), (SatisfactionRatings), (TicketFields), (Topics)],
        ids=["Macros", "Groups", "SatisfactionRatings", "TicketFields", "Topics"],
    )
    def test_parse_response(self, requests_mock, stream_cls):
        stream = stream_cls(**STREAM_ARGS)
        expected = [{"updated_at": "2022-03-17T16:03:07Z"}]
        response_field = stream.name

        requests_mock.get(STREAM_URL, json={response_field: expected})
        test_response = requests.get(STREAM_URL)

        output = list(stream.parse_response(test_response, None))

        expected = expected if isinstance(expected, list) else [expected]
        assert expected == output

    def test_attribute_definition_parse_response(self, requests_mock):
        stream = AttributeDefinitions(**STREAM_ARGS)
        conditions_all = {"subject": "number_of_incidents", "title": "Number of incidents"}
        conditions_any = {"subject": "brand", "title": "Brand"}
        response_json = {"definitions": {"conditions_all": [conditions_all], "conditions_any": [conditions_any]}}
        requests_mock.get(STREAM_URL, json=response_json)
        test_response = requests.get(STREAM_URL)
        output = list(stream.parse_response(test_response, None))
        expected_records = [
            {"condition": "all", "subject": "number_of_incidents", "title": "Number of incidents"},
            {"condition": "any", "subject": "brand", "title": "Brand"},
        ]
        assert expected_records == output

    @pytest.mark.parametrize(
        "stream_cls",
        [(Macros), (Organizations), (Groups), (SatisfactionRatings), (TicketFields), (Topics)],
        ids=["Macros", "Organizations", "Groups", "SatisfactionRatings", "TicketFields", "Topics"],
    )
    def test_url_base(self, stream_cls):
        stream = get_stream_instance(stream_cls, STREAM_ARGS)
        result = stream.url_base
        assert result == URL_BASE

    @pytest.mark.parametrize(
        "stream_cls, current_state, last_record, expected",
        [
            (Macros, {}, {"updated_at": "2022-03-17T16:03:07Z"}, {"updated_at": "2022-03-17T16:03:07Z"}),
            (
                Organizations,
                {"updated_at": "2022-03-17T16:03:07Z"},
                {"updated_at": "2023-03-17T16:03:07Z"},
                {"updated_at": "2023-03-17T16:03:07Z"},
            ),
            (Groups, {}, {"updated_at": "2022-03-17T16:03:07Z"}, {"updated_at": "2022-03-17T16:03:07Z"}),
            (SatisfactionRatings, {}, {"updated_at": "2022-03-17T16:03:07Z"}, {"updated_at": "2022-03-17T16:03:07Z"}),
            (TicketFields, {}, {"updated_at": "2022-03-17T16:03:07Z"}, {"updated_at": "2022-03-17T16:03:07Z"}),
            (Topics, {}, {"updated_at": "2022-03-17T16:03:07Z"}, {"updated_at": "2022-03-17T16:03:07Z"}),
        ],
        ids=["Macros", "Organizations", "Groups", "SatisfactionRatings", "TicketFields", "Topics"],
    )
    def test_get_updated_state(self, stream_cls, current_state, last_record, expected):
        stream = get_stream_instance(stream_cls, STREAM_ARGS)
        result = stream.get_updated_state(current_state, last_record)
        assert expected == result

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (Macros, None),
            (Organizations, {}),
            (Groups, None),
            (TicketFields, None),
        ],
        ids=[
            "Macros",
            "Organizations",
            "Groups",
            "TicketFields",
        ],
    )
    def test_next_page_token(self, stream_cls, expected, mocker):
        stream = stream_cls(**STREAM_ARGS)
        response = mocker.Mock()
        response.json.return_value = {"next_page": None}
        result = stream.next_page_token(response=response)
        assert expected == result

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (Macros, {"start_time": 1622505600}),
            (Organizations, {"start_time": 1622505600}),
            (Groups, {"start_time": 1622505600}),
            (TicketFields, {"start_time": 1622505600}),
        ],
        ids=[
            "Macros",
            "Organizations",
            "Groups",
            "TicketFields",
        ],
    )
    def test_request_params(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.request_params(stream_state={})
        assert expected == result


class TestSourceZendeskSupportFullRefreshStream:
    @pytest.mark.parametrize(
        "stream_cls",
        [
            (Tags),
            (SlaPolicies),
            (Brands),
            (CustomRoles),
            (Schedules),
            (UserSettingsStream),
            (AccountAttributes),
            (AttributeDefinitions),
            (Categories),
            (Sections),
        ],
        ids=[
            "Tags",
            "SlaPolicies",
            "Brands",
            "CustomRoles",
            "Schedules",
            "UserSettingsStream",
            "AccountAttributes",
            "AttributeDefinitions",
            "Categories",
            "Sections",
        ],
    )
    def test_url_base(self, stream_cls):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.url_base
        assert result == URL_BASE

    @pytest.mark.parametrize(
        "stream_cls",
        [
            (Tags),
            (SlaPolicies),
            (Brands),
            (CustomRoles),
            (Schedules),
            (UserSettingsStream),
            (AccountAttributes),
            (AttributeDefinitions),
        ],
        ids=[
            "Tags",
            "SlaPolicies",
            "Brands",
            "CustomRoles",
            "Schedules",
            "UserSettingsStream",
            "AccountAttributes",
            "AttributeDefinitions",
        ],
    )
    def test_next_page_token(self, requests_mock, stream_cls):
        stream = stream_cls(**STREAM_ARGS)
        stream_name = snake_case(stream.__class__.__name__)
        requests_mock.get(STREAM_URL, json={stream_name: {}})
        test_response = requests.get(STREAM_URL)
        output = stream.next_page_token(test_response)
        assert output is None

    @pytest.mark.parametrize(
        "stream_cls, expected_params",
        [
            (Tags, {"page[size]": 100}),
            (SlaPolicies, {}),
            (Brands, {"page[size]": 100}),
            (CustomRoles, {}),
            (Schedules, {"page[size]": 100}),
            (UserSettingsStream, {}),
            (AccountAttributes, {}),
            (AttributeDefinitions, {}),
        ],
        ids=[
            "Tags",
            "SlaPolicies",
            "Brands",
            "CustomRoles",
            "Schedules",
            "UserSettingsStream",
            "AccountAttributes",
            "AttributeDefinitions",
        ],
    )
    def test_request_params(self, stream_cls, expected_params):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.request_params(next_page_token=None, stream_state=None)
        assert expected_params == result


class TestSourceZendeskSupportCursorPaginationStream:
    @pytest.mark.parametrize(
        "stream_cls, current_state, last_record, expected",
        [
            (GroupMemberships, {}, {"updated_at": "2022-03-17T16:03:07Z"}, {"updated_at": "2022-03-17T16:03:07Z"}),
            (TicketForms, {}, {"updated_at": "2023-03-17T16:03:07Z"}, {"updated_at": "2023-03-17T16:03:07Z"}),
            (TicketMetricEvents, {}, {"time": "2024-03-17T16:03:07Z"}, {"time": "2024-03-17T16:03:07Z"}),
            (TicketAudits, {}, {"created_at": "2025-03-17T16:03:07Z"}, {"created_at": "2025-03-17T16:03:07Z"}),
            (OrganizationMemberships, {}, {"updated_at": "2025-03-17T16:03:07Z"}, {"updated_at": "2025-03-17T16:03:07Z"}),
            (TicketSkips, {}, {"updated_at": "2025-03-17T16:03:07Z"}, {"updated_at": "2025-03-17T16:03:07Z"}),
        ],
        ids=[
            "GroupMemberships",
            "TicketForms",
            "TicketMetricEvents",
            "TicketAudits",
            "OrganizationMemberships",
            "TicketSkips",
        ],
    )
    def test_get_updated_state(self, stream_cls, current_state, last_record, expected):
        stream = get_stream_instance(stream_cls, STREAM_ARGS)
        result = stream.get_updated_state(current_state, last_record)
        assert expected == result

    @pytest.mark.parametrize(
        "stream_cls, response, expected",
        [
            (GroupMemberships, {}, None),
            (TicketForms, {}, None),
            (
                TicketMetricEvents,
                {
                    "meta": {"has_more": True, "after_cursor": "<after_cursor>", "before_cursor": "<before_cursor>"},
                    "links": {
                        "prev": "https://subdomain.zendesk.com/api/v2/ticket_metrics.json?page%5Bbefore%5D=<before_cursor>%3D&page%5Bsize%5D=2",
                        "next": "https://subdomain.zendesk.com/api/v2/ticket_metrics.json?page%5Bafter%5D=<after_cursor>%3D&page%5Bsize%5D=2",
                    },
                },
                {"page[after]": "<after_cursor>"},
            ),
            (TicketAudits, {}, None),
            (SatisfactionRatings, {}, None),
            (
                OrganizationMemberships,
                {
                    "meta": {"has_more": True, "after_cursor": "<after_cursor>", "before_cursor": "<before_cursor>"},
                    "links": {
                        "prev": "https://subdomain.zendesk.com/api/v2/ticket_metrics.json?page%5Bbefore%5D=<before_cursor>%3D&page%5Bsize%5D=2",
                        "next": "https://subdomain.zendesk.com/api/v2/ticket_metrics.json?page%5Bafter%5D=<after_cursor>%3D&page%5Bsize%5D=2",
                    },
                },
                {"page[after]": "<after_cursor>"},
            ),
            (
                TicketSkips,
                {
                    "meta": {"has_more": True, "after_cursor": "<after_cursor>", "before_cursor": "<before_cursor>"},
                    "links": {
                        "prev": "https://subdomain.zendesk.com/api/v2/ticket_metrics.json?page%5Bbefore%5D=<before_cursor>%3D&page%5Bsize%5D=2",
                        "next": "https://subdomain.zendesk.com/api/v2/ticket_metrics.json?page%5Bafter%5D=<after_cursor>%3D&page%5Bsize%5D=2",
                    },
                },
                {"page[after]": "<after_cursor>"},
            ),
        ],
        ids=[
            "GroupMemberships",
            "TicketForms",
            "TicketMetricEvents",
            "TicketAudits",
            "SatisfactionRatings",
            "OrganizationMemberships",
            "TicketSkips",
        ],
    )
    def test_next_page_token(self, requests_mock, stream_cls, response, expected):
        stream = stream_cls(**STREAM_ARGS)
        requests_mock.get(STREAM_URL, json=response)
        test_response = requests.get(STREAM_URL)
        output = stream.next_page_token(test_response)
        assert output == expected

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (GroupMemberships, 1622505600),
            (TicketForms, 1622505600),
            (TicketMetricEvents, 1622505600),
            (TicketAudits, 1622505600),
            (OrganizationMemberships, 1622505600),
            (TicketSkips, 1622505600),
        ],
        ids=["GroupMemberships", "TicketForms", "TicketMetricEvents", "TicketAudits", "OrganizationMemberships", "TicketSkips"],
    )
    def test_check_stream_state(self, stream_cls, expected):
        stream = get_stream_instance(stream_cls, STREAM_ARGS)
        result = stream.get_stream_state_value()
        assert result == expected

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (GroupMemberships, {"page[size]": 100, "sort_by": "asc", "start_time": 1622505600}),
            (TicketForms, {"start_time": 1622505600}),
            (TicketMetricEvents, {"page[size]": 100, "start_time": 1622505600}),
            (TicketAudits, {"sort_by": "created_at", "sort_order": "desc", "limit": 200}),
            (SatisfactionRatings, {"page[size]": 100, "sort_by": "created_at", "start_time": 1622505600}),
            (OrganizationMemberships, {"page[size]": 100, "start_time": 1622505600}),
            (TicketSkips, {"page[size]": 100, "start_time": 1622505600}),
        ],
        ids=[
            "GroupMemberships",
            "TicketForms",
            "TicketMetricEvents",
            "TicketAudits",
            "SatisfactionRatings",
            "OrganizationMemberships",
            "TicketSkips",
        ],
    )
    def test_request_params(self, stream_cls, expected):
        stream = get_stream_instance(stream_cls, STREAM_ARGS)
        result = stream.request_params(stream_state=None, next_page_token=None)
        assert expected == result


class TestSourceZendeskSupportTicketEventsExportStream:
    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (TicketComments, True),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_update_event_from_record(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.update_event_from_record
        assert result == expected

    @pytest.mark.parametrize(
        "stream_cls",
        [
            (TicketComments),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_parse_response(self, requests_mock, stream_cls):
        stream = stream_cls(**STREAM_ARGS)
        stream_name = snake_case(stream.__class__.__name__)
        requests_mock.get(STREAM_URL, json={stream_name: []})
        test_response = requests.get(STREAM_URL)
        output = list(stream.parse_response(test_response))
        assert output == []

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (TicketComments, "created_at"),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_cursor_field(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.cursor_field
        assert result == expected

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (TicketComments, "ticket_events"),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_response_list_name(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.response_list_name
        assert result == expected

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (TicketComments, "child_events"),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_response_target_entity(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.response_target_entity
        assert result == expected

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (TicketComments, ["via_reference_id", "ticket_id", "timestamp"]),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_list_entities_from_event(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.list_entities_from_event
        assert result == expected

    @pytest.mark.parametrize(
        "stream_cls, expected",
        [
            (TicketComments, "Comment"),
        ],
        ids=[
            "TicketComments",
        ],
    )
    def test_event_type(self, stream_cls, expected):
        stream = stream_cls(**STREAM_ARGS)
        result = stream.event_type
        assert result == expected


def test_read_ticket_metric_events_request_params(requests_mock):
    first_page_response = {
        "ticket_metric_events": [
            {"id": 1, "ticket_id": 123, "metric": "agent_work_time", "instance_id": 0, "type": "measure", "time": "2020-01-01T01:00:00Z"},
            {
                "id": 2,
                "ticket_id": 123,
                "metric": "pausable_update_time",
                "instance_id": 0,
                "type": "measure",
                "time": "2020-01-01T01:00:00Z",
            },
            {"id": 3, "ticket_id": 123, "metric": "reply_time", "instance_id": 0, "type": "measure", "time": "2020-01-01T01:00:00Z"},
            {
                "id": 4,
                "ticket_id": 123,
                "metric": "requester_wait_time",
                "instance_id": 1,
                "type": "activate",
                "time": "2020-01-01T01:00:00Z",
            },
        ],
        "meta": {"has_more": True, "after_cursor": "<after_cursor>", "before_cursor": "<before_cursor>"},
        "links": {
            "prev": "https://subdomain.zendesk.com/api/v2/incremental/ticket_metric_events.json?page%5Bbefore%5D=<before_cursor>&page%5Bsize%5D=100&start_time=1577836800",
            "next": "https://subdomain.zendesk.com/api/v2/incremental/ticket_metric_events.json?page%5Bafter%5D=<after_cursor>&page%5Bsize%5D=100&start_time=1577836800",
        },
        "end_of_stream": False,
    }

    second_page_response = {
        "ticket_metric_events": [
            {
                "id": 5163373143183,
                "ticket_id": 130,
                "metric": "reply_time",
                "instance_id": 1,
                "type": "fulfill",
                "time": "2022-07-18T16:39:48Z",
            },
            {
                "id": 5163373143311,
                "ticket_id": 130,
                "metric": "requester_wait_time",
                "instance_id": 0,
                "type": "measure",
                "time": "2022-07-18T16:39:48Z",
            },
        ],
        "meta": {"has_more": False, "after_cursor": "<before_cursor>", "before_cursor": "<before_cursor>"},
        "links": {
            "prev": "https://subdomain.zendesk.com/api/v2/incremental/ticket_metric_events.json?page%5Bbefore%5D=<before_cursor>&page%5Bsize%5D=100&start_time=1577836800",
            "next": "https://subdomain.zendesk.com/api/v2/incremental/ticket_metric_events.json?page%5Bbefore%5D=<before_cursor>&page%5Bsize%5D=100&start_time=1577836800",
        },
        "end_of_stream": True,
    }
    request_history = requests_mock.get(
        "https://subdomain.zendesk.com/api/v2/incremental/ticket_metric_events",
        [{"json": first_page_response}, {"json": second_page_response}],
    )
    stream = TicketMetricEvents(subdomain="subdomain", start_date="2020-01-01T00:00:00Z")
    read_full_refresh(stream)
    assert request_history.call_count == 2
    assert request_history.last_request.qs == {"page[after]": ["<after_cursor>"], "page[size]": ["100"], "start_time": ["1577836800"]}


@pytest.mark.parametrize("status_code", [(403), (404)])
def test_read_tickets_comment(requests_mock, status_code):
    request_history = requests_mock.get(
        "https://subdomain.zendesk.com/api/v2/incremental/ticket_events.json", status_code=status_code, json={"error": "wrong permissions"}
    )
    stream = TicketComments(subdomain="subdomain", start_date="2020-01-01T00:00:00Z")
    read_full_refresh(stream)
    assert request_history.call_count == 1


def test_read_ticket_audits_504_error(requests_mock, caplog):
    requests_mock.get("https://subdomain.zendesk.com/api/v2/ticket_audits", status_code=504, text="upstream request timeout")
    stream = TicketAudits(subdomain="subdomain", start_date="2020-01-01T00:00:00Z")
    expected_message = "Skipping stream `ticket_audits`. Timed out waiting for response: upstream request timeout..."
    read_full_refresh(stream)
    assert expected_message in (record.message for record in caplog.records if record.levelname == "ERROR")


@pytest.mark.parametrize(
    "start_date, stream_state, audits_response, expected",
    [
        ("2020-01-01T00:00:00Z", {}, [{"created_at": "2020-01-01T00:00:00Z"}], True),
        ("2020-01-01T00:00:00Z", {}, [{"created_at": "1990-01-01T00:00:00Z"}], False),
        ("2020-01-01T00:00:00Z", {"created_at": "2021-01-01T00:00:00Z"}, [{"created_at": "2022-01-01T00:00:00Z"}], True),
        ("2020-01-01T00:00:00Z", {"created_at": "2021-01-01T00:00:00Z"}, [{"created_at": "1990-01-01T00:00:00Z"}], False),
    ],
)
def test_validate_response_ticket_audits(start_date, stream_state, audits_response, expected):
    stream = TicketAudits(subdomain="subdomain", start_date=start_date)
    response_mock = Mock()
    response_mock.json.return_value = {"audits": audits_response}
    assert stream._validate_response(response_mock, stream_state) == expected


@pytest.mark.parametrize(
    "audits_response, expected",
    [
        ({"no_audits": []}, False),
        ({}, False),
    ],
)
def test_validate_response_ticket_audits_handle_empty_response(audits_response, expected):
    stream = TicketAudits(subdomain="subdomain", start_date="2020-01-01T00:00:00Z")
    response_mock = Mock()
    response_mock.json.return_value = audits_response
    assert stream._validate_response(response_mock, {}) == expected
