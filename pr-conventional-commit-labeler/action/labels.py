# Copyright (C) 2024 Greenbone AG
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import asyncio
import re
import os
from sre_compile import dis
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import NoReturn, Optional

from pontos.changelog.conventional_commits import ConventionalCommits
from pontos.github.actions import Console
from pontos.github.api import GitHubAsyncRESTApi
import tomlkit


def parse_arguments() -> Namespace:
    parser = ArgumentParser()
    parser.add_argument("--token", required=False)
    parser.add_argument("--label-config", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--working-directory", type=Path, required=True)
    parser.add_argument("--pull-request", required=True)
    return parser.parse_args()


class LabelsError(Exception):
    pass


class Labels:
    def __init__(
        self,
        *,
        repository: str,
        token: Optional[str],
        working_directory: Path,
        group_label_config: str,
        pull_request: Optional[str] = None,
    ) -> None:
        self.repository = repository
        self.token = token
        self.working_directory = working_directory
        self.api = GitHubAsyncRESTApi(token)
        self.group_lavel_config = group_label_config
        self.pull_request = pull_request

    async def run(self) -> None:
        os.chdir(self.working_directory)
        config_file = (self.working_directory / "changelog.toml").absolute()
        Console.log(f"using change log: {config_file}")
        ccl_config = (
            self.working_directory / self.group_lavel_config
        ).absolute()
        Console.log(f"using label configuration: {ccl_config}")

        collector = ConventionalCommits(
            config=config_file if config_file.exists() else None,
        )

        changelog_groups = [
            x.get("group", "") for x in collector.commit_types()
        ]
        Console.debug(f"got conventional commit groups {changelog_groups}")
        cclc = tomlkit.parse(ccl_config.read_text(encoding="utf-8"))
        labels = cclc.get("labels", [])
        groups = cclc.get("groups", [])
        disable_on = cclc.get("disable_on")
        labels_key = set(map(lambda x: x.get("name", ""), labels))
        only_highest = cclc.get("only_highest_priority", False)
        # verify that groups and labels are known
        for x in groups:
            group = x["group"]
            if group not in changelog_groups:
                raise LabelsError(f"{group} not found in {changelog_groups}")
            label = x["label"]
            if label not in labels_key:
                raise LabelsError(f"{label} not found in {labels_key}")
        # create lookup:
        expressions = [
            (
                commit_type["group"],
                re.compile(rf'{commit_type["message"]}\s?[:|-]', flags=re.I),
            )
            for commit_type in collector.commit_types()
        ]
        lookup = []
        for g in groups:
            for l in labels:
                if g["label"] == l["name"]:
                    g_name = g["group"]
                    matcher = next(x for (g, x) in expressions if g == g_name)
                    lookup.append((matcher, l))
                    break

        async with self.api as api:
            if not self.pull_request:
                raise LabelsError("no PR identifier found")
            original_pr_labels = set(
                [
                    l
                    async for l in api.labels.get_all(
                        self.repository, self.pull_request
                    )
                ]
            )
            if disable_on and any(disable_on in x for x in original_pr_labels):
                Console.log(
                    f"skipping because {self.pull_request} contains {disable_on}"
                )
                return
            unique_labels = original_pr_labels.difference(labels_key)
            if unique_labels:
                Console.debug(f"keeping labels: {unique_labels}")
            commits = [
                c.commit.message
                async for c in api.pull_requests.commits(
                    self.repository, self.pull_request
                )
            ]

            labels = []
            for matcher, label in lookup:
                for c in commits:
                    if matcher.match(c):
                        labels.append(label)
                        break
            labels = sorted(
                labels, key=lambda x: x.get("priority", 0), reverse=True
            )
            if only_highest:
                labels = labels[:1]
            labels = set(l["name"] for l in labels)
            labels.update(unique_labels)
            Console.log(f"set labels: {labels}")
            # unlike the descriptions hints they're not overridden
            # TODO replace with
            # await api.labels.delete_all(
            #    self.repository, self.pull_request
            # )
            # once
            # https://github.com/greenbone/pontos/pull/996
            # is merged
            u = f"/repos/{self.repository}/issues/{self.pull_request}/labels"
            response = await api.labels._client.delete(u)
            response.raise_for_status()
            await api.labels.set_all(
                self.repository, self.pull_request, list(labels)
            )


def main() -> NoReturn:
    args = parse_arguments()
    try:
        asyncio.run(
            Labels(
                repository=args.repository,
                token=args.token or os.environ.get("TOKEN", ""),
                group_label_config=args.label_config,
                pull_request=args.pull_request,
                working_directory=args.working_directory,
            ).run()
        )
        sys.exit(0)
    except LabelsError as e:
        Console.error(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
