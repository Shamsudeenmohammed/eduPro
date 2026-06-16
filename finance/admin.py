from django.contrib import admin
from .models import FeePayment, FeeStructure, PayrollRecord, StudentFee

admin.site.register(FeeStructure)
admin.site.register(StudentFee)
admin.site.register(FeePayment)
admin.site.register(PayrollRecord)
