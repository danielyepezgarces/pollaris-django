import json
import re
from pathlib import Path
import yaml
from django.conf import settings
from django.core.management.base import BaseCommand
from polls.services.translation_converter import TranslationConverter


class CleanDumper(yaml.SafeDumper):
    pass


def str_representer(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


CleanDumper.add_representer(str, str_representer)


class Command(BaseCommand):
    help = 'Import JSON translations from Translatewiki to YAML'

    def handle(self, *args, **options):
        project_dir = Path(settings.BASE_DIR)
        translations_dir = project_dir / 'translations'
        import_dir = project_dir / 'translatewiki'

        if not import_dir.exists():
            self.stderr.write(self.style.ERROR('The translatewiki directory does not exist. Run translatewiki_export first.'))
            return

        if not translations_dir.exists():
            translations_dir.mkdir(parents=True, exist_ok=True)

        files = list(import_dir.glob('*.json'))
        count = 0

        for file_path in files:
            filename = file_path.name
            m = re.match(r'^([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_-]+)\.json$', filename)
            if not m:
                continue

            domain, locale = m.group(1), m.group(2)

            with open(file_path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                except Exception as e:
                    self.stderr.write(self.style.WARNING(f'Skipping {filename}: Invalid JSON ({e}).'))
                    continue

            if not isinstance(data, dict):
                self.stderr.write(self.style.WARNING(f'Skipping {filename}: Invalid JSON content.'))
                continue

            # Load English source translation file as template to find original plural variable names
            english_source_path = translations_dir / f"{domain}+intl-icu.en_GB.yaml"
            english_data = {}
            if english_source_path.exists():
                try:
                    with open(english_source_path, 'r', encoding='utf-8') as ef:
                        loaded = yaml.safe_load(ef)
                        if isinstance(loaded, dict):
                            english_data = loaded
                except Exception:
                    pass

            converted_data = {}
            for key, value in data.items():
                if isinstance(value, str):
                    english_message = english_data.get(key)
                    converted_data[key] = TranslationConverter.convert_mediawiki_to_icu(value, locale, english_message)
                else:
                    converted_data[key] = value

            import_path = translations_dir / f"{domain}+intl-icu.{locale}.yaml"
            with open(import_path, 'w', encoding='utf-8') as out_f:
                yaml.dump(converted_data, out_f, Dumper=CleanDumper, sort_keys=False, allow_unicode=True)

            self.stdout.write(f'Imported {filename} to {import_path.name}')
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully imported {count} translation files.'))
