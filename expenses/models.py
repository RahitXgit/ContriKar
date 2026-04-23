import uuid
from django.db import models


class User(models.Model):
    """Registered user identified by employee ID."""
    employee_id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'users'
        managed = False

    def __str__(self):
        return f"{self.name} ({self.employee_id})"


class Expense(models.Model):
    """A single expense entry."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_by = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='expenses_paid',
        db_column='paid_by'
    )
    split_mode = models.TextField(default='equal')  # 'equal' or 'itemized'
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'expenses'
        managed = False
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.description} — ₹{self.amount}"


class ExpenseSplit(models.Model):
    """Individual share of an expense (equal split mode)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expense = models.ForeignKey(
        Expense, on_delete=models.CASCADE,
        related_name='splits'
    )
    employee = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='expense_shares',
        db_column='employee_id'
    )
    share_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'expense_splits'
        managed = False

    def __str__(self):
        return f"{self.employee.name} owes ₹{self.share_amount} for {self.expense.description}"


class ExpenseItem(models.Model):
    """Individual line item in an itemized expense."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expense = models.ForeignKey(
        Expense, on_delete=models.CASCADE,
        related_name='items'
    )
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'expense_items'
        managed = False

    def __str__(self):
        return f"{self.description} — ₹{self.price}"


class ExpenseItemSplit(models.Model):
    """Who shares an individual item in an itemized expense."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        ExpenseItem, on_delete=models.CASCADE,
        related_name='item_splits'
    )
    employee = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='item_shares',
        db_column='employee_id'
    )
    share_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'expense_item_splits'
        managed = False

    def __str__(self):
        return f"{self.employee.name} owes ₹{self.share_amount} for {self.item.description}"


class Settlement(models.Model):
    """A settlement payment between two users."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    paid_by = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='settlements_paid',
        db_column='paid_by'
    )
    paid_to = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='settlements_received',
        db_column='paid_to'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'settlements'
        managed = False
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.paid_by.name} → {self.paid_to.name}: ₹{self.amount}"
