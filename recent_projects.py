#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Dict
from xml.etree import ElementTree

BREAK_CHARACTERS = ["_", "-"]


class AlfredMod:
    def __init__(self, arg, subtitle, valid: bool = True):
        self.valid = valid
        self.arg = arg
        self.subtitle = subtitle


class AlfredItem:
    def __init__(self, title, subtitle, arg, valid=True, vars: Optional[Dict] = None,
                 autocomplete=None, alfred_type="file"):
        self.title = title
        self.subtitle = subtitle
        self.arg = arg
        self.type = alfred_type
        self.autocomplete = autocomplete if autocomplete is not None else f"|{subtitle}|"
        self.valid = valid

        if vars is not None:
            self.variables = vars

    def add_mod(self, key_combination: str, action: AlfredMod):
        if not hasattr(self, 'mods'):
            # noinspection PyAttributeOutsideInit
            self.mods = {}

        self.mods[key_combination] = action


class AlfredOutput:
    def __init__(self, items, vars: Optional[Dict] = None):
        if vars is not None:
            self.variables = vars

        self.items = items

    def to_json(self):
        return CustomEncoder().encode(self.__dict__)


class CustomEncoder(json.JSONEncoder):
    def default(self, o):
        return o.__dict__


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

    @property
    def name(self) -> str:
        return self.display_name if self.display_name else self.folder_name


def load_product(keyword):
    try:
        with open('products.json', 'r') as outfile:
            data = json.load(outfile)

            product = Product(keyword, **data[keyword])
            return product

    except IOError:
        sys.stdout.write("Can't open products file")
    except KeyError:
        sys.stdout.write("App '{}' is not found in the products.json".format(keyword))
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


def manage_entry(project: Project, app: Product):
    open_item = AlfredItem(f"Open {project.name} in {app.name}",
                           f"Open this project in the IDE",
                           project.path, autocomplete="")

    items = []
    if is_process_running(app.keyword):
        items.append(AlfredItem(f"⚠️ Quit {app.name} to see all options", f"Action this item to go back to main list",
                                "", False, autocomplete=""))
        items.append(open_item)
    else:
        items.append(AlfredItem(f"Remove {project.name} from list", f"The project will remain on your drive",
                                project.path, vars={"remove_from_list": True}, autocomplete=""))
        items.append(AlfredItem(f"🛑Delete {project.name} from disk",
                                f"The project will be moved to the trash and removed from the list",
                                project.path, vars={"delete_from_disk": True}, autocomplete=""))
        items.append(open_item)
        items.append(AlfredItem(f"⬅︎ Go back", f"Action this item to go back to main list",
                                "", False, autocomplete=""))

    for item in items:
        autocomplete_mod = AlfredMod("", "Press ⇥ (tab) to return to main list", False)

        item.add_mod("alt", autocomplete_mod)

    output = AlfredOutput(items, {"app_keyword": app.keyword})

    sys.stdout.write(output.to_json())

def get_projects(app_keyword):
    app = load_product(app_keyword)
    recent_projects_file = find_recentprojects_file(app)
    projects = list(map(Project, read_projects_from_file(recent_projects_file)))
    return app, projects

def list_options_in_alfred(app_keyword, query):
    try:
        app, projects = get_projects(app_keyword)

        # Special autocomplete actions
        matches = re.findall(r'\|(.+?)\|', query)
        if matches and len(matches) > 0:
            project_path = matches[0]
            project = [project for project in projects if project.path == project_path][0]
            manage_entry(project, app)
            return

        projects = filter_and_sort_projects(query, projects)
        items = [AlfredItem(project.name, project.path, project.path) for project
                 in projects]

        for item in items:
            autocomplete_mod = AlfredMod("", "Press ⇥ (tab) to manage item", False)

            item.add_mod("alt", autocomplete_mod)

        output = AlfredOutput(items, {"app_keyword": app_keyword})

        sys.stdout.write(output.to_json())

    except ValueError:
        open_item = AlfredItem(f"Open {app_keyword}",
                               f"This is a backup option since no preferences were found for {app_keyword}",
                               "", autocomplete="")


        output = AlfredOutput([open_item], {"app_keyword": app_keyword})

        sys.stdout.write(output.to_json())
    except FileNotFoundError:
        open_item = AlfredItem(f"Open {app_keyword}",
                               f"This is a backup option since the projects file was not found for {app_keyword}",
                               "", autocomplete="")

        output = AlfredOutput([open_item], {"app_keyword": app_keyword})

        sys.stdout.write(output.to_json())


def open_app(app_keyword, args=None):
    try:
        app = load_product(app_keyword)

        # if the app is open, but the project isn't, then we need to manually bring it forward
        os.system(f'''osascript -e 'tell application "{app.name}" to activate' ''')

        cmd = ['open', '-nb', app.bundle_id]
        if args:
            cmd.extend(['--args'] + list(args))
        subprocess.run(cmd)

    except subprocess.CalledProcessError:
        sys.stdout.write("Can't open {}".format(app_keyword))
        exit(1)

def remove_project(app_keyword, file):
    # TODO: write code to actually remove entry from XML

    sys.stdout.write(file)

def main():  # pragma: nocover
    parser = argparse.ArgumentParser(description='A script for working with recent projects in Jetbrains IDEs')
    subparsers = parser.add_subparsers(dest='command', help=None, metavar='')

    ls_parser = subparsers.add_parser('ls', help='List recent projects from IDE')
    ls_parser.add_argument("app_keyword", help="The name of the application")
    ls_parser.add_argument("query", help="The query from Alfred")

    rm_parser = subparsers.add_parser('rm', help='Remove items')
    rm_parser.add_argument('app_keyword', help='The name of the application')
    rm_parser.add_argument('file', help='The file to remove')

    open_parser = subparsers.add_parser('open', help='Open an app')
    open_parser.add_argument('app_keyword', help='The name of the application')
    open_parser.add_argument('args', nargs='*', help='Optional arguments to pass when opening an app')

    args = parser.parse_args()

    if args.command == 'ls':
        list_options_in_alfred(args.app_keyword, args.query)
    elif args.command == 'rm':
        remove_project(args.app_keyword, args.file)
    elif args.command == 'open':
        open_app(args.app_keyword, args.args)
    else:
        parser.print_help()


if __name__ == "__main__":  # pragma: nocover
    main()
