import secrets
from django.db import models
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.utils import timezone


def generate_hex_id(length=20):
    return secrets.token_hex(length // 2) if length % 2 == 0 else secrets.token_hex((length + 1) // 2)[:length]


class UserManager(BaseUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError("The Username must be set")
        email = self.normalize_email(email) if email else None
        user = self.model(username=username, email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("roles", ["ROLE_USER", "ROLE_ADMIN"])
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractBaseUser):
    MAX_USERNAME_LENGTH = 100
    MAX_REALNAME_LENGTH = 255
    MAX_EMAIL_LENGTH = 255

    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    username = models.CharField(max_length=MAX_USERNAME_LENGTH, unique=True)
    wikimedia_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    real_name = models.CharField(max_length=MAX_REALNAME_LENGTH, null=True, blank=True)
    email = models.CharField(max_length=MAX_EMAIL_LENGTH, null=True, blank=True)
    roles = models.JSONField(default=list)
    password = models.CharField(max_length=255)

    last_login = None

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.username

    @property
    def display_name(self):
        return self.real_name if self.real_name else self.username

    @property
    def is_staff(self):
        return "ROLE_ADMIN" in (self.roles or [])

    @property
    def is_superuser(self):
        return "ROLE_ADMIN" in (self.roles or [])

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff


class Poll(models.Model):
    MAX_TITLE_LENGTH = 200
    MAX_AUTHOR_NAME_LENGTH = 100
    MAX_SLUG_LENGTH = 20
    DEFAULT_TYPE = "classic"
    DEFAULT_EDIT_VOTE_MODE = "own"
    DEFAULT_TIMEZONE_MODE = "browser"

    id = models.CharField(max_length=20, primary_key=True, default=generate_hex_id, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    slug = models.CharField(max_length=MAX_SLUG_LENGTH, unique=True, null=True, blank=True)
    admin_token = models.CharField(max_length=20, default=generate_hex_id)
    type = models.CharField(max_length=20, default=DEFAULT_TYPE)
    title = models.CharField(max_length=MAX_TITLE_LENGTH)
    localized_titles = models.JSONField(null=True, blank=True)
    description = models.TextField()
    localized_descriptions = models.JSONField(null=True, blank=True)
    password = models.CharField(max_length=255, default="", blank=True)
    is_password_for_votes_only = models.BooleanField(default=False)
    author_name = models.CharField(max_length=MAX_AUTHOR_NAME_LENGTH)
    author_email = models.CharField(max_length=255)
    locale = models.CharField(max_length=10, default="fr_FR")
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_polls", db_column="owner_id"
    )
    duration = models.IntegerField(null=True, blank=True)
    timezone_mode = models.CharField(max_length=20, default=DEFAULT_TIMEZONE_MODE)
    timezone_name = models.CharField(max_length=100, default="UTC")
    min_wikimedia_account_age_months = models.IntegerField(null=True, blank=True)
    min_wikimedia_edits_project = models.CharField(max_length=50, null=True, blank=True)
    min_wikimedia_edits_count = models.IntegerField(null=True, blank=True)
    is_publicly_listed = models.BooleanField(default=False)
    max_votes = models.IntegerField(null=True, blank=True)
    notify_on_votes = models.BooleanField(default=False)
    notify_on_comments = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    are_results_public = models.BooleanField(default=True)
    edit_vote_mode = models.CharField(max_length=20, default=DEFAULT_EDIT_VOTE_MODE)
    disable_maybe = models.BooleanField(default=False)
    vote_no_by_default = models.BooleanField(default=False)

    class Meta:
        db_table = "poll"
        verbose_name = "Poll"
        verbose_name_plural = "Polls"

    def __str__(self):
        return self.title

    @property
    def is_closed(self):
        return self.closed_at is not None and self.closed_at <= timezone.now()

    @property
    def is_date_poll(self):
        return self.type == "date"

    @property
    def is_classic_poll(self):
        return self.type == "classic"

    def get_proposals_by_dates(self):
        dates_and_choices = {}
        for proposal in self.proposals.select_related('date').all():
            date = proposal.date
            if not date or not date.value:
                continue
            date_key = date.value.strftime('%Y-%m-%d')
            if date_key not in dates_and_choices:
                dates_and_choices[date_key] = [date, []]
            dates_and_choices[date_key][1].append(proposal)

        for key in dates_and_choices:
            dates_and_choices[key][1].sort(key=lambda p: p.id)

        sorted_keys = sorted(dates_and_choices.keys())
        return [dates_and_choices[k] for k in sorted_keys]

    @property
    def cohosts(self):
        return self.pollcohost_set.all().select_related('user')

    @property
    def preferred_choices(self):
        max_yes = 0
        preferred = []
        for prop in self.proposals.all():
            count_yes = prop.answers.filter(value='yes').count()
            if count_yes == 0:
                continue
            if count_yes > max_yes:
                max_yes = count_yes
                preferred = [prop]
            elif count_yes == max_yes:
                preferred.append(prop)
        return preferred

    def get_authors_display(self):
        authors = [self.author_name]
        for ch in self.cohosts:
            authors.append(ch.user.display_name)
        if len(authors) == 1:
            return authors[0]
        return ", ".join(authors[:-1]) + " y " + authors[-1]

    def can_administer(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        if self.owner_id == user.id:
            return True
        return self.pollcohost_set.filter(user=user, cohost_right='full').exists()


class Date(models.Model):
    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    value = models.DateTimeField()
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="dates", db_column="poll_id")

    class Meta:
        db_table = "date"
        verbose_name = "Date"
        verbose_name_plural = "Dates"

    def __str__(self):
        return str(self.value)


class Proposal(models.Model):
    MAX_LABEL_LENGTH = 200

    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    label = models.CharField(max_length=MAX_LABEL_LENGTH)
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="proposals", db_column="poll_id")
    date = models.ForeignKey(
        Date, on_delete=models.CASCADE, null=True, blank=True, related_name="proposals", db_column="date_id"
    )
    start_time = models.CharField(max_length=5, null=True, blank=True)
    end_time = models.CharField(max_length=5, null=True, blank=True)

    class Meta:
        db_table = "proposal"
        verbose_name = "Proposal"
        verbose_name_plural = "Proposals"

    def __str__(self):
        return self.label


class Vote(models.Model):
    MAX_AUTHOR_NAME_LENGTH = 100

    id = models.CharField(max_length=20, primary_key=True, default=generate_hex_id, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    author_name = models.CharField(max_length=MAX_AUTHOR_NAME_LENGTH)
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="votes", db_column="poll_id")
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="votes", db_column="owner_id"
    )

    class Meta:
        db_table = "vote"
        verbose_name = "Vote"
        verbose_name_plural = "Votes"

    def get_answer_for_proposal(self, proposal):
        prop_id = proposal.id if hasattr(proposal, 'id') else proposal
        if not hasattr(self, '_answers_cache'):
            self._answers_cache = {a.proposal_id: a for a in self.answers.all()}
        return self._answers_cache.get(prop_id)

    def __str__(self):
        return f"{self.author_name} on {self.poll_id}"


class Answer(models.Model):
    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    value = models.CharField(max_length=10, null=True, blank=True)
    vote = models.ForeignKey(Vote, on_delete=models.CASCADE, related_name="answers", db_column="vote_id")
    proposal = models.ForeignKey(
        Proposal, on_delete=models.CASCADE, related_name="answers", db_column="proposal_id"
    )

    class Meta:
        db_table = "answer"
        verbose_name = "Answer"
        verbose_name_plural = "Answers"

    def __str__(self):
        return f"{self.vote_id}: {self.proposal_id} -> {self.value}"


class Comment(models.Model):
    MAX_AUTHOR_NAME_LENGTH = 100

    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    author_name = models.CharField(max_length=MAX_AUTHOR_NAME_LENGTH)
    content = models.TextField()
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="comments", db_column="poll_id")

    class Meta:
        db_table = "comment"
        verbose_name = "Comment"
        verbose_name_plural = "Comments"

    def __str__(self):
        return f"Comment by {self.author_name} on {self.poll_id}"


class PollCohost(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, db_column="poll_id")
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column="user_id")
    cohost_right = models.CharField(max_length=10, default="edit")
    pk = models.CompositePrimaryKey("poll", "user")

    class Meta:
        db_table = "poll_cohost"
        verbose_name = "Poll Cohost"
        verbose_name_plural = "Poll Cohosts"
