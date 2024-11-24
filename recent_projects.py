#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree

BREAK_CHARACTERS = ["_", "-"]


class AlfredMod:
    def __init__(self, arg, subtitle, valid: bool = True):
        self.valid = valid
        self.arg = arg
        self.subtitle = subtitle


class AlfredVarsCollection:
    def __init__(self):
        self.variables = []

    def add(self, key, value):
        self.variables.append({key: value})


class AlfredItem:
    def __init__(self, title, subtitle, arg, vars_collection: Optional[AlfredVarsCollection] = None,
                 alfred_type="file"):
        self.title = title
        self.subtitle = subtitle
        self.arg = arg
        self.type = alfred_type
        self.autocomplete = subtitle

        if vars_collection is not None:
            self.variables = vars_collection.variables

    def add_mod(self, key_combination: str, action: AlfredMod):
        if not hasattr(self, 'mods'):
            # noinspection PyAttributeOutsideInit
            self.mods = {}

        self.mods[key_combination] = action


class AlfredOutput:
    def __init__(self, items, vars_collection: Optional[AlfredVarsCollection] = None):
        if vars_collection is not None:
            self.variables = vars_collection.variables

        self.items = items

    def to_json(self):
        return CustomEncoder().encode(self.__dict__)


class CustomEncoder(json.JSONEncoder):
    def default(self, o):
        return o.__dict__


def create_json(projects, bundle_id):
    return CustomEncoder().encode(
        AlfredOutput(
            [AlfredItem(project.name, project.path, f'open -nb {bundle_id} --args {project.path}') for project in
             projects], bundle_id))


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


@dataclass
class Product:
    keyword: str
    uid: str
    folder_name: str
    bundle_id: str
    display_name: Optional[str] = None
    preferences_path: str = "~/Library/Application Support/JetBrains/"

    def name(self) -> str:
        return self.display_name if self.display_name else self.folder_name


def load_product(app):
    try:
        with open('products.json', 'r') as outfile:
            data = json.load(outfile)

            product = Product(app, **data[app])
            return product

    except IOError:
        print("Can't open products file")
    except KeyError:
        print("App '{}' is not found in the products.json".format(app))
    exit(1)


def find_recentprojects_file(application: Product):
    preferences_path = os.path.expanduser(application.preferences_path)
    most_recent_preferences = max(find_preferences_folders(preferences_path, application))
    return "{}{}/options/{}.xml".format(preferences_path, most_recent_preferences, "recentProjects")


def find_preferences_folders(preferences_path, application: Product):
    return [folder_name for folder_name in next(os.walk(preferences_path))[1] if
            application.folder_name in folder_name and not should_ignore_folder(folder_name)]


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


def list_projects(app_name, query):
    try:
        app = load_product(app_name)
        recent_projects_file = find_recentprojects_file(app)

        projects = list(map(Project, read_projects_from_file(recent_projects_file)))
        projects = filter_and_sort_projects(query, projects)

        items = [AlfredItem(project.name, project.path, f'open -nb {app.bundle_id} --args {project.path}') for project
                 in projects]

        for item in items:
            # TODO: this is slow, so I need a different method.
            #  Either do it once and cache, or do it somewhere later in the user flow
            if is_process_running(app_name):
                remove_mod = AlfredMod("", f"Please quit {app.name()} to use the remove feature", False)
            else:
                script = f'python3 recent_projects.py rm {app.keyword} "$@"'
                remove_mod = AlfredMod(script, "Remove from list (keep on hard drive)", True)

            item.add_mod("cmd+shift", remove_mod)

        output = AlfredOutput(items)

        print(output.to_json())
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

    args = parser.parse_args()

    if args.command == 'ls':
        list_projects(args.app_name, args.query)
    elif args.command == 'rm':
        # The apps will write to recentProjects.xml on quitting, so we shouldn't modify that file if the app is open
        need_to_quit = is_process_running(args.app_name)
        if need_to_quit:
            print("You need to quit before removing.")
            exit(1)
        else:
            print("You can remove this item!")
    else:
        parser.print_help()


if __name__ == "__main__":  # pragma: nocover
    main()
