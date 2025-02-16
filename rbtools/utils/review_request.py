from __future__ import unicode_literals

import logging
import re

from rbtools.api.errors import APIError
from rbtools.clients.errors import InvalidRevisionSpecError
from rbtools.deprecation import RemovedInRBTools40Warning
from rbtools.utils.match_score import Score
from rbtools.utils.repository import get_repository_id
from rbtools.utils.users import get_user


def get_draft_or_current_value(field_name, review_request):
    """Returns the draft or current field value from a review request.

    If a draft exists for the supplied review request, return the draft's
    field value for the supplied field name, otherwise return the review
    request's field value for the supplied field name.
    """
    if review_request.draft:
        fields = review_request.draft[0]
    else:
        fields = review_request

    return fields[field_name]


def get_possible_matches(review_requests, summary, description, limit=5):
    """Returns a sorted list of tuples of score and review request.

    Each review request is given a score based on the summary and
    description provided. The result is a sorted list of tuples containing
    the score and the corresponding review request, sorted by the highest
    scoring review request first.
    """
    candidates = []

    # Get all potential matches.
    for review_request in review_requests.all_items:
        summary_pair = (get_draft_or_current_value('summary', review_request),
                        summary)
        description_pair = (get_draft_or_current_value('description',
                                                       review_request),
                            description)
        score = Score.get_match(summary_pair, description_pair)
        candidates.append((score, review_request))

    # Sort by summary and description on descending rank.
    sorted_candidates = sorted(
        candidates,
        key=lambda m: (m[0].summary_score, m[0].description_score),
        reverse=True
    )

    return sorted_candidates[:limit]


def get_revisions(tool, cmd_args):
    """Returns the parsed revisions from the command line arguments.

    These revisions are used for diff generation and commit message
    extraction. They will be cached for future calls.
    """
    # Parse the provided revisions from the command line and generate
    # a spec or set of specialized extra arguments that the SCMClient
    # can use for diffing and commit lookups.
    try:
        revisions = tool.parse_revision_spec(cmd_args)
    except InvalidRevisionSpecError:
        if not tool.supports_diff_extra_args:
            raise

        revisions = None

    return revisions


def find_review_request_by_change_id(api_client,
                                     api_root,
                                     repository_info=None,
                                     repository_name=None,
                                     revisions=None,
                                     repository_id=None):
    """Ask ReviewBoard for the review request ID for the tip revision.

    Note that this function calls the ReviewBoard API with the only_fields
    paramater, thus the returned review request will contain only the fields
    specified by the only_fields variable.

    If no review request is found, None will be returned instead.

    Version Changed:
        3.0:
        The ``repository_info`` and ``repository_name`` arguments were
        deprecated in favor of adding the new ``repository_id`` argument.

    Args:
        api_client (rbtools.api.client.RBClient):
            The API client.

        api_root (rbtools.api.resource.RootResource):
            The root resource of the Review Board server.

        repository_info (rbtools.clients.RepositoryInfo, deprecated):
            The repository info object.

        repository_name (unicode, deprecated):
            The repository name.

        revisions (dict):
            The parsed revision information, including the ``tip`` key.

        repository_id (int, optional):
            The repository ID to use.
    """
    assert api_client is not None
    assert api_root is not None
    assert revisions is not None

    only_fields = 'id,commit_id,changenum,status,url,absolute_url'
    change_id = revisions['tip']
    logging.debug('Attempting to find review request from tip revision ID: %s',
                  change_id)
    # Strip off any prefix that might have been added by the SCM.
    change_id = change_id.split(':', 1)[1]

    optional_args = {}

    if change_id.isdigit():
        # Populate integer-only changenum field also for compatibility
        # with older API versions
        optional_args['changenum'] = int(change_id)

    user = get_user(api_client, api_root, auth_required=True)

    if repository_info or repository_name:
        RemovedInRBTools40Warning.warn(
            'The repository_info and repository_name arguments to '
            'find_review_request_by_change_id are deprecated and will be '
            'removed in RBTools 4.0. Please change your command to use the '
            'needs_repository attribute and pass in the repository ID '
            'directly.')
        repository_id = get_repository_id(
            repository_info, api_root, repository_name)

    # Don't limit query to only pending requests because it's okay to stamp a
    # submitted review.
    review_requests = api_root.get_review_requests(repository=repository_id,
                                                   from_user=user.username,
                                                   commit_id=change_id,
                                                   only_links='self',
                                                   only_fields=only_fields,
                                                   **optional_args)

    if review_requests:
        count = review_requests.total_results

        # Only one review can be associated with a specific commit ID.
        if count > 0:
            assert count == 1, '%d review requests were returned' % count
            review_request = review_requests[0]
            logging.debug('Found review request %s with status %s',
                          review_request.id, review_request.status)

            if review_request.status != 'discarded':
                return review_request

    return None


def guess_existing_review_request(repository_info=None,
                                  repository_name=None,
                                  api_root=None,
                                  api_client=None,
                                  tool=None,
                                  revisions=None,
                                  guess_summary=None,
                                  guess_description=None,
                                  is_fuzzy_match_func=None,
                                  no_commit_error=None,
                                  submit_as=None,
                                  additional_fields=None,
                                  repository_id=None):
    """Try to guess the existing review request ID if it is available.

    The existing review request is guessed by comparing the existing
    summary and description to the current post's summary and description,
    respectively. The current post's summary and description are guessed if
    they are not provided.

    If the summary and description exactly match those of an existing
    review request, that request is immediately returned. Otherwise,
    the user is prompted to select from a list of potential matches,
    sorted by the highest ranked match first.

    Note that this function calls the ReviewBoard API with the only_fields
    paramater, thus the returned review request will contain only the fields
    specified by the only_fields variable.

    Version Changed:
        3.0:
        The ``repository_info`` and ``repository_name`` arguments were
        deprecated in favor of adding the new ``repository_id`` argument.

    Args:
        repository_info (rbtools.clients.RepositoryInfo, deprecated):
            The repository info object.

        repository_name (unicode, deprecated):
            The repository name.

        api_root (rbtools.api.resource.RootResource):
            The root resource of the Review Board server.

        api_client (rbtools.api.client.RBClient):
            The API client.

        tool (rbtools.clients.SCMClient):
            The SCM client.

        revisions (dict):
            The parsed revisions object.

        guess_summary (bool):
            Whether to attempt to guess the summary for comparison.

        guess_description (bool):
            Whether to attempt to guess the description for comparison.

        is_fuzzy_match_func (callable, optional):
            A function which can check if a review request is a match for the
            data being posted.

        no_commit_error (callable, optional):
            A function to be called when there's no local commit.

        submit_as (unicode, optional):
            A username on the server which is used for posting review requests.
            If provided, review requests owned by this user will be matched.

        additional_fields (list of unicode, optional):
            A list of additional fields to include in the fetched review
            request resource.

        repository_id (int, optional):
            The ID of the repository to match.
    """
    assert api_root is not None
    assert api_client is not None
    assert tool is not None
    assert revisions is not None

    only_fields = [
        'id', 'summary', 'description', 'draft', 'url', 'absolute_url',
        'bugs_closed', 'status', 'public'
    ]

    if additional_fields:
        only_fields += additional_fields

    if submit_as:
        username = submit_as
    else:
        user = get_user(api_client, api_root, auth_required=True)
        username = user.username

    if repository_info or repository_name:
        RemovedInRBTools40Warning.warn(
            'The repository_info and repository_name arguments to '
            'find_review_request_by_change_id are deprecated and will be '
            'removed in RBTools 4.0. Please change your command to use the '
            'needs_repository attribute and pass in the repository ID '
            'directly.')
        repository_id = get_repository_id(
            repository_info, api_root, repository_name)

    try:
        # Get only pending requests by the current user for this
        # repository.
        review_requests = api_root.get_review_requests(
            repository=repository_id,
            from_user=username,
            status='pending',
            expand='draft',
            only_fields=','.join(only_fields),
            only_links='diffs,draft',
            show_all_unpublished=True)

        if not review_requests:
            raise ValueError('No existing review requests to update for '
                             'user %s'
                             % username)
    except APIError as e:
        raise ValueError('Error getting review requests for user %s: %s'
                         % (username, e))

    summary = None
    description = None

    if not guess_summary or not guess_description:
        try:
            commit_message = tool.get_commit_message(revisions)

            if commit_message:
                if not guess_summary:
                    summary = commit_message['summary']

                if not guess_description:
                    description = commit_message['description']
            elif callable(no_commit_error):
                no_commit_error()
        except NotImplementedError:
            raise ValueError('--summary and --description are required.')

    if not summary and not description:
        return None

    possible_matches = get_possible_matches(review_requests, summary,
                                            description)
    exact_match_count = num_exact_matches(possible_matches)

    for score, review_request in possible_matches:
        # If the score is the only exact match, return the review request
        # ID without confirmation, otherwise prompt.
        if ((score.is_exact_match() and exact_match_count == 1) or
            (callable(is_fuzzy_match_func) and
             is_fuzzy_match_func(review_request))):
            return review_request

    return None


def num_exact_matches(possible_matches):
    """Returns the number of exact matches in the possible match list."""
    count = 0

    for score, request in possible_matches:
        if score.is_exact_match():
            count += 1

    return count


def parse_review_request_url(url):
    """Parse a review request URL and return its component parts.

    Args:
        url (unicode):
            The URL to parse.

    Returns:
        tuple:
        A 3-tuple consisting of the server URL, the review request ID, and the
        diff revision.
    """
    regex = (r'^(?P<server_url>https?:\/\/.*\/(?:\/s\/[^\/]+\/)?)'
             r'r\/(?P<review_request_id>\d+)'
             r'\/?(diff\/(?P<diff_id>\d+-?\d*))?\/?')
    match = re.match(regex, url)

    if match:
        server_url = match.group('server_url')
        request_id = match.group('review_request_id')
        diff_id = match.group('diff_id')
        return (server_url, request_id, diff_id)

    return (None, None, None)
