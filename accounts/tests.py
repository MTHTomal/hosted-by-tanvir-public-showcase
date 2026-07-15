from io import BytesIO
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django import forms
from PIL import Image
from accounts.models import Player, Team, validate_image_size
from accounts.admin import PlayerAdminForm, TeamAdminForm


def make_image_upload(name="image.png", content_type="image/png", image_format="PNG"):
    image_file = BytesIO()
    image = Image.new("RGB", (2, 2), color=(28, 130, 90))
    image.save(image_file, format=image_format)
    return SimpleUploadedFile(name, image_file.getvalue(), content_type=content_type)


class MockCloudinaryResource:
    """Mock object that mimics CloudinaryResource without .size attribute"""
    def __init__(self, public_id="test_image"):
        self.public_id = public_id
        # Intentionally no .size attribute to simulate CloudinaryResource behavior


class ValidateImageSizeTests(TestCase):
    """Test the validate_image_size validator handles all input types safely"""

    def test_validator_accepts_none(self):
        """Validator should accept None without crashing"""
        # Should not raise any exception
        validate_image_size(None)
        validate_image_size(None, field_name="avatar")

    def test_validator_accepts_empty_string(self):
        """Validator should accept empty string without crashing"""
        # Should not raise any exception
        validate_image_size("")
        validate_image_size("", field_name="logo")

    def test_validator_accepts_cloudinary_resource_without_size(self):
        """Validator should accept CloudinaryResource-like objects without .size attribute"""
        mock_resource = MockCloudinaryResource()

        # Should not raise any exception - this was the original bug
        validate_image_size(mock_resource)
        validate_image_size(mock_resource, field_name="avatar")

    def test_validator_rejects_oversized_uploaded_file(self):
        """Validator should reject oversized fresh uploaded files"""
        # Create a 600 KB file (exceeds 500 KB limit)
        oversized_content = b"x" * (600 * 1024)
        oversized_file = SimpleUploadedFile(
            "large_image.jpg",
            oversized_content,
            content_type="image/jpeg"
        )

        with self.assertRaises(ValidationError):
            validate_image_size(oversized_file)

    def test_validator_accepts_acceptable_size_uploaded_file(self):
        """Validator should accept properly sized fresh uploaded files"""
        acceptable_file = make_image_upload("normal_image.jpg", "image/jpeg", "JPEG")

        # Should not raise any exception
        validate_image_size(acceptable_file)

    def test_validator_rejects_non_image_uploaded_file(self):
        """Validator should reject files that only pretend to be images"""
        fake_image = SimpleUploadedFile(
            "fake_avatar.png",
            b"not really an image",
            content_type="image/png",
        )

        with self.assertRaises(ValidationError):
            validate_image_size(fake_image)


class PlayerAdminFormTests(TestCase):
    """Test PlayerAdminForm handles image validation correctly"""

    def setUp(self):
        """Create a test player for form testing"""
        self.player = Player.objects.create_user(
            username="testplayer",
            email="test@example.com",
            password="testpass123"
        )

    def test_form_rejects_oversized_avatar(self):
        """Form should reject oversized fresh avatar uploads"""
        oversized_content = b"x" * (600 * 1024)
        oversized_file = SimpleUploadedFile(
            "large_avatar.jpg",
            oversized_content,
            content_type="image/jpeg"
        )

        form_data = {
            "username": "testplayer2",
            "email": "test2@example.com",
            "password": "testpass123",
            "first_name": "",
            "last_name": "",
            "player_type": "self",
            "unique_id": "TEST0001",
            "in_game_name": "TestGamer2",
            "bio": "",
            "available_for_recruitment": False,
        }

        form = PlayerAdminForm(
            data=form_data,
            files={"avatar": oversized_file}
        )

        # Form should be invalid due to oversized image
        self.assertFalse(form.is_valid())


class TeamAdminFormTests(TestCase):
    """Test TeamAdminForm handles image validation correctly"""

    def setUp(self):
        """Create a test team for form testing"""
        captain = Player.objects.create_user(
            username="captain",
            email="captain@example.com",
            password="testpass123"
        )
        self.team = Team.objects.create(
            name="Test Team",
            captain=captain,
            is_approved=True
        )

    def test_form_rejects_oversized_logo(self):
        """Form should reject oversized fresh logo uploads"""
        oversized_content = b"x" * (600 * 1024)
        oversized_file = SimpleUploadedFile(
            "large_logo.png",
            oversized_content,
            content_type="image/png"
        )

        form_data = {
            "name": "New Team",
            "captain": self.team.captain.id,
            "description": "New team",
            "is_recruiting": False,
            "is_approved": False,
        }

        form = TeamAdminForm(
            data=form_data,
            files={"logo": oversized_file}
        )

        # Form should be invalid due to oversized image
        self.assertFalse(form.is_valid())


class AdminFormCleanMethodTests(TestCase):
    """Test that form clean methods handle CloudinaryResource objects safely"""

    def test_player_admin_form_clean_avatar_with_cloudinary_resource(self):
        """Regression test: PlayerAdminForm.clean_avatar handles CloudinaryResource without crashing"""
        player = Player.objects.create_user(
            username="captain2",
            email="captain2@example.com",
            password="testpass123",
        )
        avatar_resource = MockCloudinaryResource("existing_avatar_id")

        form = PlayerAdminForm(instance=player)
        form.cleaned_data = {
            "avatar": avatar_resource,
        }

        cleaned_avatar = form.clean_avatar()
        self.assertIs(cleaned_avatar, avatar_resource)

    def test_team_admin_form_clean_logo_with_cloudinary_resource(self):
        """Regression test: TeamAdminForm.clean_logo handles CloudinaryResource without crashing"""
        captain = Player.objects.create_user(
            username="captain3",
            email="captain3@example.com",
            password="testpass123"
        )
        team = Team.objects.create(
            name="Team For Clean Test",
            captain=captain,
            is_approved=False,
            description="Original description"
        )

        # Simulate the scenario: form with a CloudinaryResource in cleaned_data
        # This simulates what happens when editing a team with an existing Cloudinary logo
        form = TeamAdminForm(instance=team)

        # Manually populate cleaned_data as it would be after validation
        form.cleaned_data = {
            "name": team.name,
            "captain": team.captain,
            "description": "Updated description",
            "is_recruiting": True,
            "is_approved": True,
            "logo": MockCloudinaryResource("existing_logo_id")  # Simulate saved Cloudinary resource
        }

        cleaned_logo = form.clean_logo()
        self.assertIs(cleaned_logo, form.cleaned_data["logo"])


class UserFormsImageValidationTests(TestCase):
    """Test that user-facing forms handle image validation safely"""

    def setUp(self):
        """Set up test data"""
        self.player = Player.objects.create_user(
            username="testplayer_forms",
            email="forms@example.com",
            password="testpass123"
        )
        self.captain = Player.objects.create_user(
            username="captain_forms",
            email="captain_forms@example.com",
            password="testpass123"
        )
        self.team = Team.objects.create(
            name="Team Forms Test",
            captain=self.captain,
            is_approved=True
        )

    def test_player_edit_form_rejects_oversized_avatar(self):
        """PlayerProfileForm should reject oversized avatar uploads"""
        from accounts.forms import PlayerProfileForm

        oversized_content = b"x" * (600 * 1024)
        oversized_file = SimpleUploadedFile(
            "large_avatar.jpg",
            oversized_content,
            content_type="image/jpeg"
        )

        form_data = {
            "username": "testplayer_forms",
            "email": "forms@example.com",
            "first_name": "",
            "last_name": "",
            "in_game_name": "FormsTester",
            "unique_id": self.player.unique_id,
            "bio": "Test",
            "available_for_recruitment": False,
        }

        form = PlayerProfileForm(
            data=form_data,
            files={"avatar": oversized_file},
            instance=self.player
        )

        # Form should be invalid due to oversized image
        self.assertFalse(form.is_valid())

    def test_team_identity_form_rejects_oversized_logo(self):
        """TeamIdentityForm should reject oversized logo uploads"""
        from accounts.forms import TeamIdentityForm

        oversized_content = b"x" * (600 * 1024)
        oversized_file = SimpleUploadedFile(
            "large_logo.png",
            oversized_content,
            content_type="image/png"
        )

        form_data = {
            "name": "Team Forms Test",
            "logo": "",  # Will be overridden by files parameter
        }

        form = TeamIdentityForm(
            data=form_data,
            files={"logo": oversized_file},
            instance=self.team
        )

        # Form should be invalid due to oversized image
        self.assertFalse(form.is_valid())

    def test_player_edit_form_rejects_non_image_avatar(self):
        """PlayerProfileForm should reject avatar uploads with invalid image data"""
        from accounts.forms import PlayerProfileForm

        fake_avatar = SimpleUploadedFile(
            "fake_avatar.png",
            b"not really an image",
            content_type="image/png"
        )

        form_data = {
            "username": "testplayer_forms",
            "email": "forms@example.com",
            "first_name": "",
            "last_name": "",
            "in_game_name": "FormsTester",
            "unique_id": self.player.unique_id,
            "bio": "Test",
            "available_for_recruitment": False,
        }

        form = PlayerProfileForm(
            data=form_data,
            files={"avatar": fake_avatar},
            instance=self.player
        )

        self.assertFalse(form.is_valid())
        self.assertIn("avatar", form.errors)

    def test_team_identity_form_rejects_non_image_logo(self):
        """TeamIdentityForm should reject logo uploads with invalid image data"""
        from accounts.forms import TeamIdentityForm

        fake_logo = SimpleUploadedFile(
            "fake_logo.png",
            b"not really an image",
            content_type="image/png"
        )

        form = TeamIdentityForm(
            data={"name": "Team Forms Test"},
            files={"logo": fake_logo},
            instance=self.team
        )

        self.assertFalse(form.is_valid())
        self.assertIn("logo", form.errors)

    def test_staff_player_form_rejects_oversized_avatar(self):
        """StaffPlayerIdentityForm should reject oversized avatar uploads"""
        from accounts.forms import StaffPlayerIdentityForm

        oversized_content = b"x" * (600 * 1024)
        oversized_file = SimpleUploadedFile(
            "large_avatar.jpg",
            oversized_content,
            content_type="image/jpeg"
        )

        form_data = {
            "in_game_name": "StaffTester",
            "avatar": "",  # Will be overridden by files parameter
        }

        form = StaffPlayerIdentityForm(
            data=form_data,
            files={"avatar": oversized_file},
            instance=self.player
        )

        # Form should be invalid due to oversized image
        self.assertFalse(form.is_valid())

    def test_player_edit_form_clean_avatar_with_cloudinary_resource(self):
        """PlayerProfileForm.clean_avatar handles CloudinaryResource objects safely"""
        from accounts.forms import PlayerProfileForm

        form = PlayerProfileForm(instance=self.player)
        form.cleaned_data = {
            "username": "testplayer_forms",
            "email": "forms@example.com",
            "first_name": "",
            "last_name": "",
            "in_game_name": "FormsTester",
            "unique_id": self.player.unique_id,
            "bio": "Test",
            "available_for_recruitment": False,
            "avatar": MockCloudinaryResource("existing_avatar_id")
        }

        # Should not crash with AttributeError on .size
        error_occurred = False
        try:
            result = form.clean_avatar()
        except AttributeError as e:
            if "has no attribute 'size'" in str(e):
                error_occurred = True

        self.assertFalse(
            error_occurred,
            "PlayerProfileForm.clean_avatar crashed with size attribute error"
        )

    def test_team_identity_form_clean_logo_with_cloudinary_resource(self):
        """TeamIdentityForm.clean_logo handles CloudinaryResource objects safely"""
        from accounts.forms import TeamIdentityForm

        form = TeamIdentityForm(instance=self.team)
        form.cleaned_data = {
            "name": "Team Forms Test",
            "logo": MockCloudinaryResource("existing_logo_id")
        }

        # Should not crash with AttributeError on .size
        error_occurred = False
        try:
            result = form.clean_logo()
        except AttributeError as e:
            if "has no attribute 'size'" in str(e):
                error_occurred = True

        self.assertFalse(
            error_occurred,
            "TeamIdentityForm.clean_logo crashed with size attribute error"
        )

    def test_staff_player_form_clean_avatar_with_cloudinary_resource(self):
        """StaffPlayerIdentityForm.clean_avatar handles CloudinaryResource objects safely"""
        from accounts.forms import StaffPlayerIdentityForm

        form = StaffPlayerIdentityForm(instance=self.player)
        form.cleaned_data = {
            "in_game_name": "StaffTester",
            "avatar": MockCloudinaryResource("existing_avatar_id")
        }

        # Should not crash with AttributeError on .size
        error_occurred = False
        try:
            result = form.clean_avatar()
        except AttributeError as e:
            if "has no attribute 'size'" in str(e):
                error_occurred = True

        self.assertFalse(
            error_occurred,
            "StaffPlayerIdentityForm.clean_avatar crashed with size attribute error"
        )
