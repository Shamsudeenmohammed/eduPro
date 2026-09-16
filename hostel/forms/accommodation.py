"""Staff accommodation forms: Hostel / Block / Floor / Room (+ beds)."""

from django import forms

from hostel.models import Amenity, Hostel, HostelBed, HostelBlock, HostelFloor, HostelRoom

from .base import Widgets


class AmenityForm(forms.ModelForm):
    class Meta:
        model = Amenity
        fields = ["name", "description", "icon", "display_order", "is_active"]
        widgets = {
            "name": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "icon": Widgets.TEXT,
            "display_order": Widgets.NUMBER,
            "is_active": Widgets.CHECKBOX,
        }

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name:
            name = name.strip()
        return name


class HostelForm(forms.ModelForm):
    class Meta:
        model = Hostel
        fields = ["name", "location", "description", "amenities", "is_active"]
        widgets = {
            "name": Widgets.TEXT,
            "location": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "amenities": forms.SelectMultiple(attrs={"class": "form-control", "size": 6}),
            "is_active": Widgets.CHECKBOX,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amenities"].queryset = Amenity.objects.filter(is_active=True).order_by(
            "display_order", "name"
        )
        self.fields["amenities"].help_text = "Facilities available throughout this hostel."


class HostelBlockForm(forms.ModelForm):
    class Meta:
        model = HostelBlock
        fields = ["hostel", "name", "description", "is_active"]
        widgets = {
            "hostel": Widgets.SELECT,
            "name": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "is_active": Widgets.CHECKBOX,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hostel"].queryset = Hostel.objects.filter(is_active=True)


class HostelFloorForm(forms.ModelForm):
    class Meta:
        model = HostelFloor
        fields = ["block", "name", "is_active"]
        widgets = {
            "block": Widgets.SELECT,
            "name": Widgets.TEXT,
            "is_active": Widgets.CHECKBOX,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["block"].queryset = HostelBlock.objects.select_related("hostel").filter(
            is_active=True, hostel__is_active=True
        )


class HostelRoomForm(forms.ModelForm):
    """Create a room and, optionally, its beds at once."""

    bed_count = forms.IntegerField(
        label="Beds to create",
        required=False,
        min_value=0,
        max_value=50,
        initial=0,
        widget=Widgets.NUMBER,
        help_text="0 = create the room only; beds can be added in admin later.",
    )

    class Meta:
        model = HostelRoom
        fields = ["hostel", "floor", "room_number", "description", "amenities", "capacity", "is_available"]
        widgets = {
            "hostel": Widgets.SELECT,
            "floor": Widgets.SELECT,
            "room_number": Widgets.TEXT,
            "description": Widgets.TEXTAREA,
            "amenities": forms.SelectMultiple(attrs={"class": "form-control", "size": 6}),
            "capacity": Widgets.NUMBER,
            "is_available": Widgets.CHECKBOX,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hostel"].queryset = Hostel.objects.filter(is_active=True)
        self.fields["hostel"].required = True
        self.fields["floor"].required = False
        self.fields["floor"].queryset = HostelFloor.objects.select_related("block__hostel")
        self.fields["amenities"].queryset = Amenity.objects.filter(is_active=True).order_by(
            "display_order", "name"
        )
        self.fields["amenities"].help_text = "Facilities specific to this room (adds to the hostel's)."

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("floor") and cleaned.get("hostel"):
            if cleaned["floor"].block.hostel_id != cleaned["hostel"].pk:
                self.add_error(
                    "floor",
                    "The selected floor does not belong to the selected hostel.",
                )
        return cleaned

    def save(self, commit=True):
        room = super().save(commit=False)
        if commit:
            room.save()
            count = self.cleaned_data.get("bed_count") or 0
            if count:
                existing = HostelBed.objects.filter(room=room).count()
                for i in range(1, count + 1):
                    HostelBed.objects.create(
                        room=room,
                        bed_number=f"{i + existing:02d}",
                        label=f"{room.room_number}-{i + existing:02d}",
                    )
        return room