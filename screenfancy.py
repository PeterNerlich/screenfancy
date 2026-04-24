#!/usr/bin/env python3

# Adaption of screenplains main.py for custom pdf template and analytics
# Copyright (c) 2011 Martin Vilcans
# Licensed under the MIT license:
# http://www.opensource.org/licenses/mit-license.php

import sys
import re
import codecs
from optparse import OptionParser
from reportlab import platypus
from datetime import timedelta
from pprint import pp
from copy import deepcopy
from itertools import zip_longest

from screenplain import types
from screenplain.parsers import fountain
from screenplain.main import output_formats, usage, invalid_format
from screenplain.export.pdf import DocTemplate, create_default_settings, get_title_page_story, add_dialog, add_dual_dialog, add_paragraph, add_slug
from screenplain.types import (
    Action, Dialog, DualDialog, Transition, Slug
)


def compile_stats(screenplay):
    template = {
        "takes": 0,
        "words": 0,
        "chars": 0,
        "time": timedelta(),
    }
    template_characters = {
        **template,
        "scenes": set(),
    }
    template_scenes = {
        **template,
        "characters": set(),
    }

    characters = {}
    characters[None] = total_characters = deepcopy(template_characters)

    scenes = {}
    scenes[None] = total_scenes = deepcopy(template_scenes)

    current_scene = None
    current_character = None

    def add_dialog(para):
        current_character = str(para.character).removesuffix(" (CONT'D)")

        if current_character not in characters:
            characters[current_character] = deepcopy(template_characters)
        c = characters[current_character]

        if current_scene not in scenes:
            scenes[current_scene] = deepcopy(template_scenes)
        s = scenes[current_scene]

        c["takes"] += 1
        c["scenes"].add(current_scene)

        s["takes"] += 1
        s["characters"].add(current_character)

        for parenthetical, line in para.blocks:
            if not parenthetical:
                chars = len(str(line))
                c["chars"] += chars
                s["chars"] += chars
                words = str(line).split()
                c["words"] += len(words)
                s["words"] += len(words)
        
        total_characters["takes"] += 1
        total_characters["scenes"].add(current_scene)
        total_characters["words"] += c["words"]
        total_characters["chars"] += c["chars"]

        total_scenes["takes"] += 1
        total_scenes["characters"].add(current_character)
        total_scenes["words"] += s["words"]
        total_scenes["chars"] += s["chars"]

    for para in screenplay:
        if isinstance(para, Dialog):
            add_dialog(para)
        elif isinstance(para, DualDialog):
            add_dialog(para.left)
            add_dialog(para.right)
        elif isinstance(para, Action):
            pass
        elif isinstance(para, Slug):
            current_scene = ' '.join(str(l) for l in para.lines)
        else:
            # Ignore unknown types
            pass

    for character, c in characters.items():
        if character is not None:
            c["time"] += timedelta(seconds = (
                c["takes"] * 1
              + c["words"] * .2
              + c["chars"] * .025
            ))
            total_characters["time"] += c["time"]

    for scene, s in scenes.items():
        if scene is not None:
            s["time"] += timedelta(seconds = (
                s["takes"] * 1
              + s["words"] * .2
              + s["chars"] * .025
            ))
            total_scenes["time"] += s["time"]

    return {
        "characters": characters,
        "scenes": scenes,
    }

def add_character_stats(story, stat_dict, style):
    characters = stat_dict["characters"]
    pp(characters)
    stats = {
        character: [character, len(stats["scenes"]), stats["takes"], stats["time"]]
        for character, stats in characters.items()
    }
    stat_lines = [
        stat
        for character, stat in stats.items()
        if character is not None
    ]
    # Sort by speaking duration
    stat_lines.sort(key=lambda x: x[3], reverse=True)

    cells = [
        [f"{character}:", f"{takes} takes,", f"{scenes} scenes,", pretty_time(time_estimate)]
        for character, scenes, takes, time_estimate in stat_lines
    ] + [[""]*4,] + [
        ["Total:", f"{takes} takes,", f"{scenes} scenes,", pretty_time(time_estimate)]
        for character, scenes, takes, time_estimate in [stats[None]]
    ]

    max_lengths = [
        max(len(cell) for cell in column)
        for column in zip(*cells)
    ]
    lines = [
        "Characters:",
        "",
    ] + [
        ' '.join([
            f"{cell}{' '*padding}"
            for cell, padding in zip(row, paddings)
        ])
        for row in cells
        if (paddings := [
            max_length - len(cell)
            for max_length, cell in zip(max_lengths, row)
        ]) is not None
    ]
    story.append(platypus.Preformatted('\n'.join(lines), style.default_style))

    story.append(platypus.PageBreak())

def add_scene_stats(story, stat_dict, style):
    scenes = stat_dict["scenes"]
    pp(scenes)
    stats = {
        scene: [scene, stats["characters"], stats["takes"], stats["time"]]
        for scene, stats in scenes.items()
    }
    stat_lines = [
        stat
        for scene, stat in stats.items()
        if scene is not None
    ]
    # Sort by speaking duration
    stat_lines.sort(key=lambda x: x[3], reverse=True)

    # Prepare per scene character list
    character_sets = [
        scenes[scene]["characters"]
        for line in stat_lines
        if (scene := line[0])
    ]

    cells = [
        [f"{scene}:", f"{takes} takes,", f"{len(characters)} characters,", pretty_time(time_estimate)]
        for scene, characters, takes, time_estimate in stat_lines
    ] + [[""]*4 + [None]] + [
        ["Total:", f"{takes} takes,", f"{len(characters)} characters,", pretty_time(time_estimate), None]
        for scene, characters, takes, time_estimate in [stats[None]]
    ]
    max_lengths = [
        max(len(cell) for cell in column)
        for column in list(zip(*cells))
    ]
    lines = [
        ' '.join([
            f"{cell}{' '*padding}"
            for cell, padding in zip(row, paddings)
        ])
        for row in cells
        if (paddings := [
            max_length - len(cell)
            for max_length, cell in zip(max_lengths, row)
        ]) is not None
    ]

    # interleave character lists
    lines = [
        line
        for summary, lst in zip_longest(lines, character_sets)
        for line in [summary] + list(typeset_items([capitalize(i) for i in (lst or set())], seperator=",  ", width=52, padding_left=3)) + [""]
        if line is not None
    ]

    lines = [
        "Scenes:",
        "",
    ] + lines

    story.append(platypus.Preformatted('\n'.join(lines), style.default_style))

    story.append(platypus.PageBreak())


def pretty_time(delta):
    return str(delta).split('.', 2)[0]


def to_pdf(
    screenplay, output_filename,
    template_constructor=DocTemplate,
    settings=None
):
    settings = settings or create_default_settings()
    story = get_title_page_story(screenplay, settings)
    has_title_page = bool(story)

    stats = compile_stats(screenplay)
    add_character_stats(story, stats, settings)
    add_scene_stats(story, stats, settings)

    for para in screenplay:
        if isinstance(para, Dialog):
            add_dialog(story, para, settings)
        elif isinstance(para, DualDialog):
            add_dual_dialog(story, para, settings)
        elif isinstance(para, Action):
            add_paragraph(
                story, para,
                settings.centered_action_style
                if para.centered
                else settings.action_style
            )
        elif isinstance(para, Slug):
            add_slug(story, para, settings.slug_style, settings.strong_slugs)
        elif isinstance(para, Transition):
            add_paragraph(story, para, settings.transition_style)
        elif isinstance(para, types.PageBreak):
            story.append(platypus.PageBreak())
        else:
            # Ignore unknown types
            pass

    meta = pdf_metadata(screenplay)
    doc = template_constructor(
        output_filename,
        pagesize=(settings.page_width, settings.page_height),
        settings=settings,
        has_title_page=has_title_page,
        **meta,
    )
    doc.build(story)


def pdf_metadata(screenplay):
    title_lines = screenplay.get_rich_attribute("Title")
    author_lines = [
        *screenplay.get_rich_attribute("Author"),
        *screenplay.get_rich_attribute("Authors"),
    ]
    subject_lines = screenplay.get_rich_attribute("Subject")
    creator_lines = screenplay.get_rich_attribute("Creator")
    producer_lines = screenplay.get_rich_attribute("Producer")
    keywords_lines = screenplay.get_rich_attribute("Keywords")

    lang_lines = [
        *screenplay.get_rich_attribute("Lang"),
        *screenplay.get_rich_attribute("Language"),
    ]

    return {
        "title":   ' '.join([str(line) for line in title_lines]) or None,
        "subject": ' '.join([str(line) for line in subject_lines]) or None,
        "author": ', '.join([str(line) for line in author_lines]) or None,
        "creator": ' '.join([str(line) for line in creator_lines]) or None,
        "producer": ' '.join([str(line) for line in producer_lines]) or None,
        "keywords": [
            word.strip()
            for line in keywords_lines
            for word in re.split('[,;/]', str(line))
            if word.strip()
        ],
        "lang":    ' '.join([str(line) for line in lang_lines]) or None,
    }


def main(args):
    parser = OptionParser(usage=usage)
    parser.add_option(
        '-f', '--format', dest='output_format',
        metavar='FORMAT',
        help=(
            'Set what kind of file to create. FORMAT can be one of ' +
            ', '.join(output_formats)
        )
    )
    parser.add_option(
        '--bare',
        action='store_true',
        dest='bare',
        help=(
            'For HTML output, only output the actual screenplay, '
            'not a complete HTML document.'
        )
    )
    parser.add_option(
        '--css',
        metavar='FILE',
        help=(
            'For HTML output, inline the given CSS file in the HTML document '
            'instead of the default.'
        )
    )
    parser.add_option(
        '--strong',
        action='store_true',
        dest='strong',
        help=(
            'For PDF output, scene headings will appear '
            'Bold and Underlined.'
        )
    )
    parser.add_option(
        '--encoding',
        default='utf-8-sig',
        help="Text encoding of the input file. " +
        "Should be one of Python's built-in encodings."
    )
    parser.add_option(
        '--encoding-errors',
        default='strict',
        choices=['strict', 'ignore', 'replace',
                 'backslashreplace', 'surrogateescape'],
        help='How to handle invalid character codes in the input file'
    )
    options, args = parser.parse_args(args)
    if len(args) >= 3:
        parser.error('Too many arguments')
    input_file = (len(args) > 0 and args[0] != '-') and args[0] or None
    output_file = (len(args) > 1 and args[1] != '-') and args[1] or None

    try:
        codecs.lookup(options.encoding)
    except LookupError:
        parser.error('Unknown encoding: %s' % options.encoding)

    format = options.output_format
    if format is None and output_file:
        if output_file.endswith('.fdx'):
            format = 'fdx'
        elif output_file.endswith('.html'):
            format = 'html'
        elif output_file.endswith('.pdf'):
            format = 'pdf'
        else:
            invalid_format(
                parser,
                'Could not detect output format from file name ' + output_file
            )

    if format not in output_formats:
        invalid_format(
            parser, 'Unsupported output format: "%s".' % format
        )

    if input_file:
        input = codecs.open(
            input_file, 'r',
            encoding=options.encoding,
            errors=options.encoding_errors)
    else:
        input = codecs.getreader(options.encoding)(sys.stdin.buffer)
        input.errors = options.encoding_errors
    screenplay = fountain.parse(input)

    if format == 'pdf':
        output_encoding = None
    else:
        output_encoding = 'utf-8'

    if output_file:
        if output_encoding:
            output = codecs.open(output_file, 'w', output_encoding)
        else:
            output = open(output_file, 'wb')
    else:
        if output_encoding:
            output = codecs.getwriter(output_encoding)(sys.stdout.buffer)
        else:
            output = sys.stdout.buffer

    try:
        if format == 'fdx':
            from screenplain.export.fdx import to_fdx
            to_fdx(screenplay, output)
        elif format == 'html':
            from screenplain.export.html import convert
            convert(
                screenplay, output,
                css_file=options.css, bare=options.bare
            )
        elif format == 'pdf':
            settings = create_default_settings()
            settings.character_style.fontName += "-Bold"
            settings.strong_slugs = options.strong
            to_pdf(screenplay, output, template_constructor=DocTemplate, settings=settings)
    finally:
        if output_file:
            output.close()
        if input_file:
            input.close()


def typeset_items(items, seperator=' ', width=80, padding_left=0):
    seplen = len(seperator.rstrip())
    line = ""
    last_item = None
    for item in sorted(items):
        if last_item is not None:
            if (len(line) + len(last_item) + seplen)  >  (width - padding_left):
                yield " "*padding_left + line.rstrip()
                line = last_item + seperator
            else:
                line += last_item + seperator
        last_item = item

    if last_item:
        if (len(line) + len(last_item) + seplen)  >  (width - padding_left):
            yield " "*padding_left + line.rstrip()
            line = last_item
        else:
            line += last_item

    if line:
        yield " "*padding_left + line.rstrip()

def capitalize(string):
    return " ".join([
        item[:1].upper() + item[1:].lower()
        for item in string.split(" ")
    ])


def cli():
    """setup.py entry point for console scripts."""
    main(sys.argv[1:])


if __name__ == '__main__':
    main(sys.argv[1:])
