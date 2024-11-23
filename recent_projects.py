#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from xml.etree import ElementTree

BREAK_CHARACTERS = ["_", "-"]


class AlfredItem:
    def __init__(self, title, subtitle, arg, type="file"):
        self.title = title
        self.subtitle = subtitle
        self.arg = arg
        self.type = type
        self.autocomplete = subtitle


class AlfredOutput:
    def __init__(self, items, bundle_id):
        self.variables = {"bundle_id": bundle_id}
        self.items = items


class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        return obj.__dict__


def create_json(projects, bundle_id):
    return CustomEncoder().encode(
        AlfredOutput([AlfredItem(project.name, project.path, project.path) for project in projects], bundle_id))


class Project:
    def __init__(self, path):
        self.path = path
        # os.path.expanduser() is needed for os.path.isfile(), but Alfred can handle the `~` shorthand in the returned JSON.
        name_file = os.path.expanduser(self.path) + "/.idea/.name"

        if os.path.isfile(name_file):
            self.name = open(name_file).read()
        else:
            self.name = path.split('/')[-1]
        self.abbreviation = self.abbreviate()

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.name == other.name and self.path == other.path and self.abbreviation == other.abbreviation
        return False

    def abbreviate(self):
        previous_was_break = False
        abbreviation = self.name[0]
        for char in self.name[1: len(self.name)]:
            if char in BREAK_CHARACTERS:
                previous_was_break = True
            else:
                if previous_was_break:
                    abbreviation += char
                    previous_was_break = False
        return abbreviation

    def matches_query(self, query):
        return query in self.path.lower() or query in self.abbreviation.lower() or query in self.name.lower()

    def sort_on_match_type(self, query):
        if query == self.abbreviation:
            return 0
        elif query in self.name:
            return 1
        return 2


def find_app_data(app):
    try:
        with open('products.json', 'r') as outfile:
            data = json.load(outfile)
            return data[app]
    except IOError:
        print("Can't open products file")
    except KeyError:
        print("App '{}' is not found in the products.json".format(app))
    exit(1)


def find_recentprojects_file(application):
    preferences_path = os.path.expanduser(preferences_path_or_default(application))
    most_recent_preferences = max(find_preferences_folders(preferences_path, application))
    return "{}{}/options/{}.xml".format(preferences_path, most_recent_preferences, "recentProjects")


def preferences_path_or_default(application):
    return application["preferences_path"] if "preferences_path" in application \
        else "~/Library/Application Support/JetBrains/"


def find_preferences_folders(preferences_path, application):
    return [folder_name for folder_name in next(os.walk(preferences_path))[1] if
            application["folder_name"] in folder_name and not should_ignore_folder(folder_name)]


def should_ignore_folder(folder_name):
    return "backup" in folder_name


def read_projects_from_file(most_recent_projects_file):
    tree = ElementTree.parse(most_recent_projects_file)
    projects = [t.attrib['key'].replace('$USER_HOME$', "~") for t
                in tree.findall(".//component[@name='RecentProjectsManager']/option[@name='additionalInfo']/map/entry")]
    return reversed(projects)


def filter_and_sort_projects(query, projects):
    if len(query) < 1:
        return projects
    results = [p for p in projects if p.matches_query(query)]
    results.sort(key=lambda p: p.sort_on_match_type(query))
    return results

def is_process_running(app_name):
    try:
        subprocess.check_output(['/usr/bin/pgrep', '-i', '-f', app_name])
        return True
    except subprocess.CalledProcessError:
        return False

def open_app(app_name, args=None):
    try:
        app_data = find_app_data(app_name)
        bundle_id = app_data['bundle-id']

        cmd = ['open', '-nb', bundle_id]

        if args:
            cmd.extend(['--args'] + list(args))
        subprocess.run(cmd)
    except subprocess.CalledProcessError:
        print("Can't open {}".format(app_name))
        exit(1)

def list_projects(app_name, query):
    try:
        app_data = find_app_data(app_name)
        recent_projects_file = find_recentprojects_file(app_data)

        projects = list(map(Project, read_projects_from_file(recent_projects_file)))
        projects = filter_and_sort_projects(query, projects)

        print(create_json(projects, app_data["bundle_id"]))
    except IndexError:
        print("No app specified, exiting")
        exit(1)
    except ValueError:
        print("Can't find any preferences for", app_name)
        exit(1)
    except FileNotFoundError:
        print(f"The projects file for {app_name} does not exist.")
        exit(1)

def main():  # pragma: nocover
    parser = argparse.ArgumentParser(description='A script for working with recent projects in Jetbrains IDEs')
    subparsers = parser.add_subparsers(dest='command', help=None, metavar='')

    # Create parser for "ls" command
    ls_parser = subparsers.add_parser('ls', help='List recent projects from IDE')
    ls_parser.add_argument("app_name", help="The name of the application")
    ls_parser.add_argument("query", help="The query from Alfred")

    # Create parser for "rm" command
    rm_parser = subparsers.add_parser('rm', help='Remove items')
    rm_parser.add_argument('-a', '--all', action='store_true', help='Remove all items')
    rm_parser.add_argument('app_name', help='The name of the application')
    rm_parser.add_argument('file', nargs='+', help='File to remove')

    open_parser = subparsers.add_parser('open', help='Open an app')
    open_parser.add_argument('args', nargs='*', help='Optional arguments to pass when opening an app')

    args = parser.parse_args()

    if args.command == 'ls':
        list_projects(args.app_name, args.query)
    elif args.command == 'rm':
        # The apps will write to recentProjects.xml on quitting, so we shouldn't modify that file if the app is open
        need_to_quit = is_process_running(args.app_name)
        if need_to_quit:
            print(json.dumps({"items": [
                {
                    "type": "default",
                    "title": "You must quit Pycharm first",
                    "valid": False,
                }
            ]}))
        else:
            print("YAY")

        # remove_items(args)
    elif args.command == 'open':
        open_app(args.app_name, args.args)
    else:
        parser.print_help()

if __name__ == "__main__":  # pragma: nocover
    main()
