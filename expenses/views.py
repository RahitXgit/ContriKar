import json
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

from django.shortcuts import render, redirect
from django.utils import timezone

from .models import User, Expense, ExpenseSplit, ExpenseItem, ExpenseItemSplit


# ---------- helpers ----------

def get_logged_in_user(request):
    """Return the User object from session, or None."""
    eid = request.session.get('employee_id')
    if not eid:
        return None
    try:
        return User.objects.get(employee_id=eid)
    except User.DoesNotExist:
        return None


def login_required(view_fn):
    """Simple decorator — redirect to login if no session."""
    def wrapper(request, *args, **kwargs):
        if not request.session.get('employee_id'):
            return redirect('login')
        return view_fn(request, *args, **kwargs)
    return wrapper


# ---------- auth views ----------

def login_view(request):
    if request.session.get('employee_id'):
        return redirect('dashboard')

    error = ''
    if request.method == 'POST':
        employee_id = request.POST.get('employee_id', '').strip()
        if not employee_id:
            error = 'Please enter your employee ID.'
        else:
            try:
                user = User.objects.get(employee_id=employee_id)
                request.session['employee_id'] = user.employee_id
                request.session['user_name'] = user.name
                return redirect('dashboard')
            except User.DoesNotExist:
                # Not registered — redirect to register with pre-filled ID
                return redirect(f'/register/?eid={employee_id}')

    return render(request, 'login.html', {'error': error})


def register_view(request):
    if request.session.get('employee_id'):
        return redirect('dashboard')

    error = ''
    prefill_eid = request.GET.get('eid', '')

    if request.method == 'POST':
        employee_id = request.POST.get('employee_id', '').strip()
        name = request.POST.get('name', '').strip()

        if not employee_id or not name:
            error = 'Both fields are required.'
        elif User.objects.filter(employee_id=employee_id).exists():
            error = 'This employee ID is already registered. Go back and login.'
        else:
            User.objects.create(employee_id=employee_id, name=name)
            request.session['employee_id'] = employee_id
            request.session['user_name'] = name
            return redirect('dashboard')

    return render(request, 'register.html', {
        'error': error,
        'prefill_eid': prefill_eid,
    })


def logout_view(request):
    request.session.flush()
    return redirect('login')


# ---------- main views ----------

@login_required
def dashboard_view(request):
    """Show expense log + balances summary."""
    current_user = get_logged_in_user(request)
    expenses = (
        Expense.objects
        .select_related('paid_by')
        .prefetch_related(
            'splits__employee',
            'items__item_splits__employee',
        )
        .all()
    )
    all_users = User.objects.all().order_by('name')

    # Compute net balances
    balances = compute_balances()

    return render(request, 'dashboard.html', {
        'user': current_user,
        'expenses': expenses,
        'balances': balances,
        'all_users': all_users,
    })


@login_required
def add_expense_view(request):
    current_user = get_logged_in_user(request)
    all_users = User.objects.all().order_by('name')
    error = ''
    success = ''

    if request.method == 'POST':
        split_mode = request.POST.get('split_mode', 'equal').strip()
        paid_by_id = request.POST.get('paid_by', '').strip()

        # Validate payer
        payer = None
        if not paid_by_id:
            error = 'Please select who paid.'
        else:
            try:
                payer = User.objects.get(employee_id=paid_by_id)
            except User.DoesNotExist:
                error = 'Invalid payer selected.'

        if not error and split_mode == 'equal':
            error = _handle_equal_split(request, payer)
        elif not error and split_mode == 'itemized':
            error = _handle_itemized_split(request, payer)

        if not error:
            return redirect('dashboard')

    return render(request, 'add_expense.html', {
        'user': current_user,
        'all_users': all_users,
        'error': error,
        'success': success,
    })


def _handle_equal_split(request, payer):
    """Process an equal-split expense. Returns error string or empty on success."""
    description = request.POST.get('description', '').strip()
    amount_str = request.POST.get('amount', '').strip()
    split_among = request.POST.getlist('split_among')

    if not description or not amount_str or not split_among:
        return 'All fields are required. Select at least one person to split with.'

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise ValueError
    except (ValueError, Exception):
        return 'Enter a valid positive amount.'

    # Equal split
    num_people = len(split_among)
    share = (amount / Decimal(num_people)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # Handle rounding — first person gets the remainder
    total_shares = share * num_people
    remainder = amount - total_shares

    expense = Expense.objects.create(
        description=description,
        amount=amount,
        paid_by=payer,
        split_mode='equal',
    )

    for i, eid in enumerate(split_among):
        s = share
        if i == 0:
            s += remainder  # adjust first person for rounding
        ExpenseSplit.objects.create(
            expense=expense,
            employee_id=eid,
            share_amount=s,
        )

    return ''  # no error


def _handle_itemized_split(request, payer):
    """Process an itemized-split expense. Returns error string or empty on success."""
    description = request.POST.get('description', '').strip()
    item_count_str = request.POST.get('item_count', '0')

    if not description:
        return 'Please provide a description for the expense.'

    try:
        item_count = int(item_count_str)
    except ValueError:
        return 'Invalid item data.'

    if item_count <= 0:
        return 'Add at least one item.'

    # Parse items from POST data
    items_data = []
    total_amount = Decimal('0.00')

    for idx in range(item_count):
        item_desc = request.POST.get(f'item_desc_{idx}', '').strip()
        item_price_str = request.POST.get(f'item_price_{idx}', '').strip()
        item_assigned = request.POST.getlist(f'item_assigned_{idx}')

        # Skip removed items (gaps in indices from JS removal)
        if not item_desc and not item_price_str:
            continue

        if not item_desc or not item_price_str:
            return f'Item: description and price are required.'

        try:
            item_price = Decimal(item_price_str)
            if item_price <= 0:
                raise ValueError
        except (ValueError, Exception):
            return f'Item "{item_desc}": enter a valid positive price.'

        if not item_assigned:
            return f'Item "{item_desc}": select at least one person.'

        items_data.append({
            'description': item_desc,
            'price': item_price,
            'assigned': item_assigned,
        })
        total_amount += item_price

    if not items_data:
        return 'Add at least one item.'

    # Create the expense
    expense = Expense.objects.create(
        description=description,
        amount=total_amount,
        paid_by=payer,
        split_mode='itemized',
    )

    # Create items and their splits
    for item_data in items_data:
        item = ExpenseItem.objects.create(
            expense=expense,
            description=item_data['description'],
            price=item_data['price'],
        )

        num_people = len(item_data['assigned'])
        share = (item_data['price'] / Decimal(num_people)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        total_shares = share * num_people
        remainder = item_data['price'] - total_shares

        for i, eid in enumerate(item_data['assigned']):
            s = share
            if i == 0:
                s += remainder
            ExpenseItemSplit.objects.create(
                item=item,
                employee_id=eid,
                share_amount=s,
            )

    return ''  # no error


@login_required
def settle_view(request):
    """Compute minimum transactions to settle all debts."""
    current_user = get_logged_in_user(request)
    balances = compute_balances()
    transactions = compute_settlements(balances)

    return render(request, 'settle.html', {
        'user': current_user,
        'transactions': transactions,
        'balances': balances,
    })


# ---------- balance computation ----------

def compute_balances():
    """
    Compute net balance for every user.
    Handles both 'equal' (expense_splits) and 'itemized' (expense_item_splits) modes.
    Positive = others owe you (creditor).
    Negative = you owe others (debtor).
    """
    net = defaultdict(Decimal)

    for expense in Expense.objects.prefetch_related(
        'splits', 'items__item_splits'
    ).all():
        payer_id = expense.paid_by_id

        if expense.split_mode == 'itemized':
            # Sum shares from expense_item_splits
            for item in expense.items.all():
                for item_split in item.item_splits.all():
                    if item_split.employee_id != payer_id:
                        net[payer_id] += item_split.share_amount
                        net[item_split.employee_id] -= item_split.share_amount
        else:
            # Equal mode — read from expense_splits
            for split in expense.splits.all():
                if split.employee_id != payer_id:
                    net[payer_id] += split.share_amount
                    net[split.employee_id] -= split.share_amount

    # Build result with user objects
    result = []
    for user in User.objects.all().order_by('name'):
        bal = net.get(user.employee_id, Decimal('0.00'))
        result.append({
            'user': user,
            'balance': bal,
            'status': 'gets back' if bal > 0 else ('owes' if bal < 0 else 'settled'),
        })

    return result


def compute_settlements(balances):
    """
    Greedy algorithm: match largest creditor with largest debtor repeatedly.
    Returns list of {from_user, to_user, amount}.
    """
    creditors = []  # (user, amount)
    debtors = []    # (user, abs_amount)

    for b in balances:
        if b['balance'] > 0:
            creditors.append([b['user'], b['balance']])
        elif b['balance'] < 0:
            debtors.append([b['user'], abs(b['balance'])])

    # Sort descending by amount
    creditors.sort(key=lambda x: x[1], reverse=True)
    debtors.sort(key=lambda x: x[1], reverse=True)

    transactions = []
    i, j = 0, 0

    while i < len(creditors) and j < len(debtors):
        creditor, credit = creditors[i]
        debtor, debt = debtors[j]
        settle_amount = min(credit, debt)

        if settle_amount > 0:
            transactions.append({
                'from_user': debtor,
                'to_user': creditor,
                'amount': settle_amount.quantize(Decimal('0.01')),
            })

        creditors[i][1] -= settle_amount
        debtors[j][1] -= settle_amount

        if creditors[i][1] == 0:
            i += 1
        if debtors[j][1] == 0:
            j += 1

    return transactions
