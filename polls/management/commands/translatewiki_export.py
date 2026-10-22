import json
import re
from pathlib import Path
import yaml
from django.conf import settings
from django.core.management.base import BaseCommand
from polls.services.translation_converter import TranslationConverter


class Command(BaseCommand):
    help = 'Export Symfony/Django YAML translations to JSON for Translatewiki'

    def handle(self, *args, **options):
        project_dir = Path(settings.BASE_DIR)
        translations_dir = project_dir / 'translations'
        export_dir = project_dir / 'translatewiki'

        if not export_dir.exists():
            export_dir.mkdir(parents=True, exist_ok=True)

        files = list(translations_dir.glob('*+intl-icu.*.yaml'))
        count = 0

        for file_path in files:
            filename = file_path.name
            m = re.match(r'^([a-zA-Z0-9_-]+)\+intl-icu\.([a-zA-Z0-9_-]+)\.yaml$', filename)
            if not m:
                continue

            domain, locale = m.group(1), m.group(2)

            with open(file_path, 'r', encoding='utf-8') as f:
                yaml_content = yaml.safe_load(f)

            if not isinstance(yaml_content, dict):
                continue

            converted_data = {}
            for key, value in yaml_content.items():
                if isinstance(value, str):
                    converted_data[key] = TranslationConverter.convert_icu_to_mediawiki(value, locale)
                else:
                    converted_data[key] = value

            export_path = export_dir / f"{domain}.{locale}.json"
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(converted_data, f, indent=4, ensure_ascii=False)

            self.stdout.write(f'Exported {filename} to {export_path.name}')
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully exported {count} translation files.'))
