from __future__ import unicode_literals

import logging

from rbtools.api.errors import APIError
from rbtools.commands import Command, CommandError, Option


class Publish(Command):
    """Publish a specific review request from a draft."""

    name = 'publish'
    author = 'The Review Board Project'

    needs_api = True

    args = '<review-request-id>'
    option_list = [
        Command.server_options,
        Command.repository_options,
        Option('-t', '--trivial',
               dest='trivial_publish',
               action='store_true',
               default=False,
               help='Publish the review request without sending an e-mail '
                    'notification.',
               added_in='1.0'),
        Option('--markdown',
               dest='markdown',
               action='store_true',
               config_key='MARKDOWN',
               default=False,
               help='Specifies if the change description should should be '
                    'interpreted as Markdown-formatted text.',
               added_in='1.0'),
        Option('-m', '--change-description',
               dest='change_description',
               default=None,
               help='The change description to use for the publish.',
               added_in='1.0'),
    ]

    def main(self, review_request_id):
        """Run the command."""
        try:
            review_request = self.api_root.get_review_request(
                review_request_id=review_request_id,
                only_fields='public',
                only_links='draft')
        except APIError as e:
            raise CommandError('Error getting review request %s: %s'
                               % (review_request_id, e))

        update_fields = {
            'public': True,
        }

        if (self.options.trivial_publish and
            self.capabilities.has_capability('review_requests',
                                             'trivial_publish')):
            update_fields['trivial'] = True

        if self.options.change_description is not None:
            if review_request.public:
                update_fields['changedescription'] = \
                    self.options.change_description

                if (self.options.markdown and
                    self.capabilities.has_capability('text', 'markdown')):
                    update_fields['changedescription_text_type'] = 'markdown'
                else:
                    update_fields['changedescription_text_type'] = 'plain'
            else:
                logging.error(
                    'The change description field can only be set when '
                    'publishing an update.')

        try:
            draft = review_request.get_draft(only_fields='')
            draft.update(**update_fields)
        except APIError as e:
            raise CommandError('Error publishing review request (it may '
                               'already be published): %s' % e)

        self.stdout.write('Review request #%s is published.'
                          % review_request_id)
        self.json.add('review_request', review_request_id)
