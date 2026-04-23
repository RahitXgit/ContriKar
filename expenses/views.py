import json
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.http import HttpResponseForbidden

from .models import User, Expense, ExpenseSplit, ExpenseItem, ExpenseItemSplit, Settlement

# ---------- admin config ----------
ADMIN_EMPLOYEE_ID = '3199815'


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


def is_admin(user):
    """Check if user has admin privileges."""
    if user is None:
        return False
    return user.employee_id == ADMIN_EMPLOYEE_ID


def login_required(view_fn):
    """Redirect to login if no session OR if user no longer exists in DB."""
    def wrapper(request, *args, **kwargs):
        eid = request.session.get('employee_id')
        if not eid:
            return redirect('login')
        # Verify user still exists in DB (handles DB wipe / stale session)
        if not User.objects.filter(employee_id=eid).exists():
            request.session.flush()
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
        'is_admin': is_admin(current_user),
    })


@login_required
def all_expenses_view(request):
    """Show all expenses in descending order (newest first)."""
    current_user = get_logged_in_user(request)
    expenses = (
        Expense.objects
        .select_related('paid_by')
        .prefetch_related(
            'splits__employee',
            'items__item_splits__employee',
        )
        .order_by('-created_at')
    )

    # Compute total across all expenses
    total_spent = sum(e.amount for e in expenses)

    return render(request, 'all_expenses.html', {
        'user': current_user,
        'expenses': expenses,
        'total_spent': total_spent,
        'expense_count': expenses.count(),
        'is_admin': is_admin(current_user),
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
    """Compute pairwise net debts to settle all balances."""
    current_user = get_logged_in_user(request)
    balances = compute_balances()
    transactions = compute_pairwise_settlements()

    # Settlement history — newest first
    settlement_history = (
        Settlement.objects
        .select_related('paid_by', 'paid_to')
        .order_by('-created_at')
    )

    return render(request, 'settle.html', {
        'user': current_user,
        'transactions': transactions,
        'balances': balances,
        'settlement_history': settlement_history,
    })


@login_required
def record_settlement(request):
    """Record a settlement payment between two users."""
    if request.method != 'POST':
        return redirect('settle')

    paid_by_id = request.POST.get('paid_by', '').strip()
    paid_to_id = request.POST.get('paid_to', '').strip()
    amount_str = request.POST.get('amount', '').strip()
    note = request.POST.get('note', '').strip() or None

    if not paid_by_id or not paid_to_id or not amount_str:
        return redirect('settle')

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise ValueError
    except (ValueError, Exception):
        return redirect('settle')

    try:
        payer = User.objects.get(employee_id=paid_by_id)
        payee = User.objects.get(employee_id=paid_to_id)
    except User.DoesNotExist:
        return redirect('settle')

    Settlement.objects.create(
        paid_by=payer,
        paid_to=payee,
        amount=amount,
        note=note,
    )

    return redirect('settle')


# ---------- admin views ----------

@login_required
def delete_expense_view(request, expense_id):
    """Admin-only: delete an expense and all associated splits/items."""
    current_user = get_logged_in_user(request)
    if not is_admin(current_user):
        return HttpResponseForbidden('Admin access required.')

    expense = get_object_or_404(Expense, id=expense_id)

    if request.method == 'POST':
        # Delete child records first (items, item_splits, splits)
        for item in expense.items.all():
            item.item_splits.all().delete()
            item.delete()
        expense.splits.all().delete()
        expense.delete()
        return redirect('dashboard')

    # GET — show confirmation page (shouldn't normally be hit via UI)
    return redirect('dashboard')


@login_required
def edit_expense_view(request, expense_id):
    """Admin-only: edit an existing expense."""
    current_user = get_logged_in_user(request)
    if not is_admin(current_user):
        return HttpResponseForbidden('Admin access required.')

    expense = get_object_or_404(
        Expense.objects.select_related('paid_by').prefetch_related(
            'splits__employee', 'items__item_splits__employee'
        ),
        id=expense_id,
    )
    all_users = User.objects.all().order_by('name')
    error = ''

    if request.method == 'POST':
        split_mode = expense.split_mode
        paid_by_id = request.POST.get('paid_by', '').strip()

        payer = None
        if not paid_by_id:
            error = 'Please select who paid.'
        else:
            try:
                payer = User.objects.get(employee_id=paid_by_id)
            except User.DoesNotExist:
                error = 'Invalid payer selected.'

        if not error and split_mode == 'equal':
            description = request.POST.get('description', '').strip()
            amount_str = request.POST.get('amount', '').strip()
            split_among = request.POST.getlist('split_among')

            if not description or not amount_str or not split_among:
                error = 'All fields are required.'
            else:
                try:
                    amount = Decimal(amount_str)
                    if amount <= 0:
                        raise ValueError
                except (ValueError, Exception):
                    error = 'Enter a valid positive amount.'

            if not error:
                # Update expense
                expense.description = description
                expense.amount = amount
                expense.paid_by = payer
                expense.save()

                # Delete old splits and recreate
                expense.splits.all().delete()

                num_people = len(split_among)
                share = (amount / Decimal(num_people)).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                total_shares = share * num_people
                remainder = amount - total_shares

                for i, eid in enumerate(split_among):
                    s = share
                    if i == 0:
                        s += remainder
                    ExpenseSplit.objects.create(
                        expense=expense,
                        employee_id=eid,
                        share_amount=s,
                    )

                return redirect('dashboard')

        elif not error and split_mode == 'itemized':
            description = request.POST.get('description', '').strip()
            item_count_str = request.POST.get('item_count', '0')

            if not description:
                error = 'Please provide a description.'

            try:
                item_count = int(item_count_str)
            except ValueError:
                error = 'Invalid item data.'

            if not error and item_count <= 0:
                error = 'Add at least one item.'

            items_data = []
            total_amount = Decimal('0.00')

            if not error:
                for idx in range(item_count):
                    item_desc = request.POST.get(f'item_desc_{idx}', '').strip()
                    item_price_str = request.POST.get(f'item_price_{idx}', '').strip()
                    item_assigned = request.POST.getlist(f'item_assigned_{idx}')

                    if not item_desc and not item_price_str:
                        continue

                    if not item_desc or not item_price_str:
                        error = 'Item: description and price are required.'
                        break

                    try:
                        item_price = Decimal(item_price_str)
                        if item_price <= 0:
                            raise ValueError
                    except (ValueError, Exception):
                        error = f'Item "{item_desc}": enter a valid positive price.'
                        break

                    if not item_assigned:
                        error = f'Item "{item_desc}": select at least one person.'
                        break

                    items_data.append({
                        'description': item_desc,
                        'price': item_price,
                        'assigned': item_assigned,
                    })
                    total_amount += item_price

            if not error and not items_data:
                error = 'Add at least one item.'

            if not error:
                # Update expense
                expense.description = description
                expense.amount = total_amount
                expense.paid_by = payer
                expense.save()

                # Delete old items and their splits
                for item in expense.items.all():
                    item.item_splits.all().delete()
                    item.delete()

                # Create new items
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

                return redirect('dashboard')

    # Build context for the edit form
    # Gather current split member IDs
    current_split_ids = []
    if expense.split_mode == 'equal':
        current_split_ids = list(
            expense.splits.values_list('employee_id', flat=True)
        )
    
    # Gather current items data for itemized mode
    current_items = []
    if expense.split_mode == 'itemized':
        for item in expense.items.all():
            current_items.append({
                'description': item.description,
                'price': item.price,
                'assigned_ids': list(
                    item.item_splits.values_list('employee_id', flat=True)
                ),
            })

    return render(request, 'edit_expense.html', {
        'user': current_user,
        'expense': expense,
        'all_users': all_users,
        'error': error,
        'is_admin': True,
        'current_split_ids': current_split_ids,
        'current_items': current_items,
        'current_items_json': json.dumps([
            {
                'description': it['description'],
                'price': str(it['price']),
                'assigned_ids': it['assigned_ids'],
            }
            for it in current_items
        ]),
    })


# ---------- balance computation ----------

def compute_balances():
    """
    Compute net balance for every user.
    Handles both 'equal' (expense_splits) and 'itemized' (expense_item_splits) modes.
    Also factors in settlements.
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

    # Factor in settlements: paid_by reduces their debt, paid_to reduces their credit
    for settlement in Settlement.objects.all():
        net[settlement.paid_by_id] += settlement.amount
        net[settlement.paid_to_id] -= settlement.amount

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


def compute_pairwise_settlements():
    """
    Compute direct pairwise net debts between users.
    For each expense, track who owes whom directly, then net out each pair.
    Also factors in settlement payments.
    Returns list of {from_user, to_user, amount}.
    """
    # pairwise[debtor_id][creditor_id] = total amount owed
    pairwise = defaultdict(lambda: defaultdict(Decimal))

    for expense in Expense.objects.prefetch_related(
        'splits', 'items__item_splits'
    ).all():
        payer_id = expense.paid_by_id

        if expense.split_mode == 'itemized':
            for item in expense.items.all():
                for item_split in item.item_splits.all():
                    if item_split.employee_id != payer_id:
                        pairwise[item_split.employee_id][payer_id] += item_split.share_amount
        else:
            for split in expense.splits.all():
                if split.employee_id != payer_id:
                    pairwise[split.employee_id][payer_id] += split.share_amount

    # Factor in settlements: settlement reduces debtor→creditor debt
    for settlement in Settlement.objects.all():
        # paid_by was the debtor, paid_to was the creditor
        pairwise[settlement.paid_by_id][settlement.paid_to_id] -= settlement.amount

    # Net out each pair and build transactions
    transactions = []
    processed = set()
    users = {u.employee_id: u for u in User.objects.all()}

    all_ids = set(pairwise.keys())
    for debtor_id in pairwise:
        all_ids.update(pairwise[debtor_id].keys())

    for a_id in all_ids:
        for b_id in all_ids:
            if a_id >= b_id:
                continue
            pair = (a_id, b_id)
            if pair in processed:
                continue
            processed.add(pair)

            a_owes_b = pairwise.get(a_id, {}).get(b_id, Decimal('0'))
            b_owes_a = pairwise.get(b_id, {}).get(a_id, Decimal('0'))
            net = a_owes_b - b_owes_a

            if net > 0:
                transactions.append({
                    'from_user': users[a_id],
                    'to_user': users[b_id],
                    'amount': net.quantize(Decimal('0.01')),
                })
            elif net < 0:
                transactions.append({
                    'from_user': users[b_id],
                    'to_user': users[a_id],
                    'amount': abs(net).quantize(Decimal('0.01')),
                })

    # Sort by amount descending for readability
    transactions.sort(key=lambda t: t['amount'], reverse=True)
    return transactions
