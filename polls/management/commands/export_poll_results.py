import json
from django.core.management.base import BaseCommand
from django.db import connection, models
from polls.models import Poll, Vote, Proposal, Answer


class Command(BaseCommand):
    help = "Exporta resultados de una encuesta por CLI y realiza diagnóstico detallado de votos y opciones."

    def add_arguments(self, parser):
        parser.add_argument("poll_identifier", type=str, help="ID o Slug de la encuesta (ej: 409900f518709b99806a)")
        parser.add_argument("--json", action="store_true", help="Salida en formato JSON")

    def handle(self, *args, **options):
        ident = options["poll_identifier"].strip()
        is_json = options.get("json", False)

        # 1. Búsqueda por ORM
        poll = Poll.objects.filter(models.Q(id=ident) | models.Q(slug=ident)).first()

        # 2. Diagnóstico Raw SQL (para detectar discrepancias de collation o id)
        raw_poll = None
        raw_votes = []
        raw_proposals = []
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, title, slug, type, closed_at, are_results_public FROM poll WHERE id = %s OR slug = %s", [ident, ident])
            raw_poll = cursor.fetchone()

            actual_poll_id = raw_poll[0] if raw_poll else ident

            cursor.execute("SELECT id, author_name, owner_id, created_at, poll_id FROM vote WHERE poll_id = %s", [actual_poll_id])
            raw_votes = cursor.fetchall()

            cursor.execute("SELECT id, label FROM proposal WHERE poll_id = %s ORDER BY id", [actual_poll_id])
            raw_proposals = cursor.fetchall()

        if not poll and not raw_poll:
            self.stderr.write(self.style.ERROR(f"No se encontro ninguna encuesta con el identificador '{ident}'."))
            self.stdout.write("Buscando encuestas similares en la base de datos...")
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, title, slug FROM poll ORDER BY created_at DESC LIMIT 10")
                recent_polls = cursor.fetchall()
                if recent_polls:
                    self.stdout.write("Ultimas encuestas en la base de datos:")
                    for r_id, r_title, r_slug in recent_polls:
                        self.stdout.write(f" - ID: {r_id} | Slug: {r_slug} | Titulo: {r_title}")
                else:
                    self.stdout.write("No hay encuestas en la tabla poll.")
            return

        poll_id = poll.id if poll else raw_poll[0]
        poll_title = poll.title if poll else raw_poll[1]
        poll_slug = poll.slug if poll else raw_poll[2]
        poll_type = poll.type if poll else raw_poll[3]

        # Propuestas
        proposals_dict = {}
        if poll:
            for p in poll.proposals.all().order_by("id"):
                proposals_dict[p.id] = {
                    "id": p.id,
                    "label": p.label,
                    "yes": 0,
                    "maybe": 0,
                    "no": 0,
                    "total": 0,
                }
        else:
            for row in raw_proposals:
                proposals_dict[row[0]] = {
                    "id": row[0],
                    "label": row[1],
                    "yes": 0,
                    "maybe": 0,
                    "no": 0,
                    "total": 0,
                }

        # Conteo de votos y respuestas
        votes_data = []
        with connection.cursor() as cursor:
            # Obtener respuestas detalladas
            cursor.execute("""
                SELECT v.id, v.author_name, v.created_at, a.proposal_id, a.value
                FROM vote v
                LEFT JOIN answer a ON a.vote_id = v.id
                WHERE v.poll_id = %s
                ORDER BY v.created_at, v.id
            """, [poll_id])
            rows = cursor.fetchall()

            votes_map = {}
            for v_id, v_author, v_created, p_id, val in rows:
                if v_id not in votes_map:
                    votes_map[v_id] = {
                        "id": v_id,
                        "author": v_author,
                        "created_at": str(v_created),
                        "answers": {},
                    }
                if p_id is not None and val:
                    votes_map[v_id]["answers"][p_id] = val
                    if p_id in proposals_dict:
                        if val == "yes":
                            proposals_dict[p_id]["yes"] += 1
                        elif val == "maybe":
                            proposals_dict[p_id]["maybe"] += 1
                        elif val == "no":
                            proposals_dict[p_id]["no"] += 1
                        proposals_dict[p_id]["total"] += 1

            votes_data = list(votes_map.values())

        # Determinar ganadora(s)
        max_yes = -1
        winners = []
        for p_id, p_stats in proposals_dict.items():
            if p_stats["yes"] > max_yes:
                max_yes = p_stats["yes"]
                winners = [p_stats]
            elif p_stats["yes"] == max_yes and max_yes > 0:
                winners.append(p_stats)

        results_payload = {
            "poll": {
                "id": poll_id,
                "title": poll_title,
                "slug": poll_slug,
                "type": poll_type,
            },
            "total_votes": len(votes_data),
            "proposals": list(proposals_dict.values()),
            "winners": winners if max_yes > 0 else [],
            "votes": votes_data,
        }

        if is_json:
            self.stdout.write(json.dumps(results_payload, indent=2, ensure_ascii=False))
            return

        # Salida formateada legible en consola
        self.stdout.write("=" * 60)
        self.stdout.write(self.style.SUCCESS(f"RESULTADOS DE LA ENCUESTA: {poll_title}"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"ID:           {poll_id}")
        self.stdout.write(f"Slug:         {poll_slug}")
        self.stdout.write(f"Tipo:         {poll_type}")
        self.stdout.write(f"Total votos:  {len(votes_data)}")
        self.stdout.write("-" * 60)

        self.stdout.write(self.style.WARNING("OPCION(ES) GANADORA(S):"))
        if winners and max_yes > 0:
            for w in winners:
                self.stdout.write(self.style.SUCCESS(f" * {w['label']} -> {w['yes']} votos Si, {w['maybe']} Tal vez, {w['no']} No"))
        else:
            self.stdout.write("  No hay votos positivos suficientes para determinar un ganador.")

        self.stdout.write("-" * 60)
        self.stdout.write("TABLA DE TODAS LAS OPCIONES:")
        for p_stats in proposals_dict.values():
            prefix = " [*]" if p_stats in winners and max_yes > 0 else " [ ]"
            self.stdout.write(f"{prefix} {p_stats['label']}: {p_stats['yes']} Si | {p_stats['maybe']} Tal vez | {p_stats['no']} No (Total: {p_stats['total']})")

        self.stdout.write("-" * 60)
        self.stdout.write(f"PARTICIPANTES ({len(votes_data)}):")
        if votes_data:
            for v in votes_data:
                self.stdout.write(f" - {v['author']} (ID voto: {v['id']}, Fecha: {v['created_at']})")
        else:
            self.stdout.write("  (No hay participantes registrados en la tabla 'vote' para este poll_id)")
            # Chequeo extra de seguridad si hay votos sueltos
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM vote")
                total_all_votes = cursor.fetchone()[0]
                cursor.execute("SELECT DISTINCT poll_id FROM vote LIMIT 10")
                sample_poll_ids = [row[0] for row in cursor.fetchall()]
                self.stdout.write(f"  Diagnostico: Total de votos en toda la base de datos = {total_all_votes}")
                self.stdout.write(f"  Muestra de poll_id existentes en la tabla vote: {sample_poll_ids}")
        self.stdout.write("=" * 60)
