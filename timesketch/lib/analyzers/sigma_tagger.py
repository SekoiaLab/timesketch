"""Index analyzer plugin for sigma."""
from __future__ import unicode_literals

import logging
import time
import elasticsearch

from flask import current_app

from timesketch.lib.analyzers import utils

from timesketch.lib.analyzers import interface
from timesketch.lib.analyzers import manager
import timesketch.lib.sigma_util as ts_sigma_lib


logger = logging.getLogger('timesketch.analyzers.sigma_tagger')


class SigmaPlugin(interface.BaseAnalyzer):
    """Analyzer for Sigma."""

    NAME = 'sigma'
    DISPLAY_NAME = 'Sigma'
    DESCRIPTION = 'Run pre-defined Sigma rules and tag matching events'

    def run_sigma_rule(self, query, rule_name, tag_list = None):
        """Runs a sigma rule and applies the appropriate tags.

        Args:
            query: elastic search query for events to tag.
            rule_name: rule_name to apply to matching events.
            tag_list: a list of additional tags to be added to the event(s)

        Returns:
            int: number of events tagged.
        """
        return_fields = []
        tagged_events_counter = 0
        events = self.event_stream(
            query_string=query, return_fields=return_fields)
        for event in events:
            ts_sigma_rules = event.source.get('ts_sigma_rule', [])
            ts_sigma_rules.append(rule_name)
            event.add_attributes({'ts_sigma_rule': list(set(ts_sigma_rules))})
            ts_ttp = event.source.get('ts_ttp', [])
            for tag in tag_list:
                # special handling for sigma tags that TS considers TTPS
                # https://car.mitre.org and https://attack.mitre.org
                if tag.startswith(('attack.', 'car.')):
                    ts_ttp.append(tag)
                    tag_list.remove(tag)
            event.add_tags(tag_list)
            if len(ts_ttp) > 0:
                event.add_attributes({'ts_ttp': list(set(ts_ttp))})
            event.commit()
            tagged_events_counter += 1
        return tagged_events_counter

    def run(self):
        """Entry point for the analyzer.

        Returns:
            String with summary of the analyzer result.
        """

        tags_applied = {}
        sigma_rule_counter = 0
        sigma_rules = ts_sigma_lib.get_all_sigma_rules()
        if sigma_rules is None:
            logger.error('No  Sigma rules found. Check SIGMA_RULES_FOLDERS')
        problem_strings = []
        output_strings = []

        for rule in sigma_rules:
            tags_applied[rule.get('file_name')] = 0
            try:
                sigma_rule_counter += 1
                tagged_events_counter = self.run_sigma_rule(
                    rule.get('es_query'), rule.get('file_name'),
                    tag_list=rule.get('tags'))
                tags_applied[rule.get('file_name')] += tagged_events_counter
                if sigma_rule_counter % 10 == 0:
                    logger.debug('Rule {0:d}/{1:d}'.format(
                        sigma_rule_counter, len(sigma_rules)))
            except elasticsearch.TransportError as e:
                sleep_time : int = current_app.config.get(
                    'SIGMA_TAG_DELAY', 15)
                logger.error(
                    'Timeout executing search for {0:s}: '
                    '{1!s} waiting for {2:d} seconds '
                    '(https://github.com/google/timesketch/issues/1782)'
                    .format(
                        rule.get('file_name'), e, sleep_time), exc_info=True)
                # this is caused by too many ES queries in short time range
                # TODO: https://github.com/google/timesketch/issues/1782
                time.sleep(sleep_time)
                tagged_events_counter = self.run_sigma_rule(
                    rule.get('es_query'), rule.get('file_name'),
                    tag_list=rule.get('tags'))
                tags_applied[rule.get('file_name')] += tagged_events_counter
            # Wide exception handling since there are multiple exceptions that
            # can be raised by the underlying sigma library.
            except: # pylint: disable=bare-except
                logger.error(
                    'Problem with rule in file {0:s}: '.format(
                        rule.get('file_name')), exc_info=True)
                problem_strings.append('* {0:s}'.format(
                    rule.get('file_name')))
                continue

        total_tagged_events = sum(tags_applied.values())
        output_strings.append('Applied {0:d} tags'.format(total_tagged_events))

        if total_tagged_events > 0:
            self.add_sigma_match_view(sigma_rule_counter)

        if len(problem_strings) > 0:
            output_strings.append('Problematic rules:')
            output_strings.extend(problem_strings)

        return '\n'.join(output_strings)

    def add_sigma_match_view(self, sigma_rule_counter):
        """Adds a view with the top 20 matching rules.

        Args:
            sigma_rule_counter number of matching rules

        """
        view = self.sketch.add_view(
            view_name='Sigma Rule matches', analyzer_name=self.NAME,
            query_string='tag:"sigma*"')
        agg_params = {
            'field': 'tag',
            'limit': 20,
            'index': [self.timeline_id],
        }
        agg_obj = self.sketch.add_aggregation(
            name='Top 20 Sigma tags', agg_name='field_bucket',
            agg_params=agg_params, view_id=view.id, chart_type='hbarchart',
            description='Created by the Sigma analyzer')

        story = self.sketch.add_story('Sigma Rule hits')
        story.add_text(
            utils.SIGMA_STORY_HEADER, skip_if_exists=True)

        story.add_text(
            '## Sigma Analyzer.\n\nThe Sigma '
            'analyzer takes Events and matches them with Sigma rules.'
            'In this timeline the analyzer discovered {0:d} '
            'Sigma tags.\n\nThis is a summary of '
            'it\'s findings.'.format(sigma_rule_counter))
        story.add_text(
            'The top 20 most commonly discovered tags were:')
        story.add_aggregation(agg_obj)
        story.add_text(
            'And an overview of all the discovered search terms:')
        story.add_view(view)


class RulesSigmaPlugin(SigmaPlugin):
    """Sigma plugin to run rules."""

    NAME = 'sigma'

manager.AnalysisManager.register_analyzer(RulesSigmaPlugin)
