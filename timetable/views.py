from itertools import groupby
from collections import OrderedDict
from datetime import date
from csv import reader as CSVReader
from io import TextIOWrapper

from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponseRedirect
from django.urls import resolve, reverse
from django.utils.translation import gettext as _
from django.utils.dateparse import parse_date
from django.views.decorators.vary import vary_on_cookie
from django.views.decorators.cache import never_cache
from django.views.generic.edit import FormView
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.decorators import login_required, permission_required
from django.conf import settings

from .models import *
from .utils import (get_timetable_context, get_schedules_table, get_days_periods,
    get_events, get_display_context, get_teacher_by_name)
from .forms import *


@vary_on_cookie
def show_timetable(request):
    """Redirects to timetable given in GET parameter or in cookies"""
    klass = request.GET.get('class')
    if klass:
        return HttpResponseRedirect('/timetable/class/'+klass+'/')

    teacher = request.GET.get('teacher')
    if teacher:
        return HttpResponseRedirect('/timetable/teacher/'+teacher+'/')

    room = request.GET.get('room')
    if room:
        return HttpResponseRedirect('/timetable/room/'+room+'/')

    user_default = request.COOKIES.get('timetable_default') # set in JS
    version = request.COOKIES.get('timetable_version')
    if user_default is None or version != settings.TIMETABLE_VERSION:
        response = HttpResponseRedirect('/timetable/class/1/')
        response.delete_cookie('timetable_default', path='/timetable/')
        response.delete_cookie('timetable_version', path='/timetable/')
        return response
    return HttpResponseRedirect(user_default)

def show_class_timetable(request, class_id):
    klass = get_object_or_404(Class, pk=class_id)
    groups = Group.objects.filter(classes=klass)
    lessons = Lesson.objects.filter(group__in=groups)
    context = get_timetable_context(lessons)
    context['class'] = klass
    context['groups'] = groups
    return render(request, 'class_timetable.html', context)

def show_groups_timetable(request, group_ids):
    try:
        group_ids = [int(n) for n in group_ids.split(',')]
    except:
        raise Http404
    groups = Group.objects.filter(pk__in=group_ids)
    if len(groups) != len(group_ids):
        raise Http404
    lessons = Lesson.objects.filter(group__in=group_ids)
    context = get_timetable_context(lessons)
    context['groups'] = groups
    return render(request, 'group_timetable.html', context)

def show_room_timetable(request, room_id):
    room = get_object_or_404(Room, pk=room_id)
    lessons = Lesson.objects.filter(room=room).prefetch_related('group__classes')
    context = get_timetable_context(lessons)
    context['room'] = room
    return render(request, 'room_timetable.html', context)

def show_teacher_timetable(request, teacher_id):
    teacher = get_object_or_404(Teacher, pk=teacher_id)
    lessons = Lesson.objects.filter(teacher=teacher).prefetch_related('group__classes')
    context = get_timetable_context(lessons)
    context['teacher'] = teacher
    context['timetable_teacher'] = teacher
    return render(request, 'teacher_timetable.html', context)

def personalize(request, class_id):
    #TODO: switch to a Django form?
    context = dict()
    if request.POST:
        groups = request.POST.getlist('group-checkbox')
        if not groups:
            context['error'] = _('Select at least one group')
        else:
            url = reverse('groups_timetable', args=[','.join(groups)])
            return HttpResponseRedirect(url)
    klass = get_object_or_404(Class, pk=class_id)
    groups = Group.objects.filter(classes=klass)
    context['class'] = klass
    context['groups'] = groups
    return render(request, 'personalization.html', context)

def show_schedules(request):
    context = get_schedules_table()
    return render(request, 'schedules.html', context)

class AddSubstitutionsView1(PermissionRequiredMixin, FormView):
    """The first step to adding a substitution

    selects a teacher and a date to be passed into the second step
    """
    permission_required = 'timetable.add_substitution'
    template_name = 'teacher_and_date_select.html'
    form_class = SelectTeacherAndDateForm

    def form_valid(self, form):
        teacher = form.cleaned_data['teacher']
        date = form.cleaned_data['date']
        return redirect('add_substitutions2', teacher.pk, str(date))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = date.today()
        end_date = date(today.year + 1, today.month, today.day)
        events = get_events(end_date=end_date)
        context['substitutions'] = events['substitutions']
        context['show_substitution_delete'] = True
        return context

@never_cache
@permission_required('timetable.add_substitution')
def add_substitutions2(request, teacher_id, date):
    date = parse_date(date)
    teacher = get_object_or_404(Teacher, pk=teacher_id)

    if request.method == 'POST':
        formset = SubstitutionFormSet(teacher, date, request.POST)
        if formset.is_valid():
            formset.save()
            return HttpResponseRedirect(reverse('add_substitutions1'))
    else:
        formset = SubstitutionFormSet(teacher, date)

    context = {
        'teacher': teacher,
        'formset': formset,
        'date': date,
    }
    return render(request, 'add_substitutions.html', context)

@never_cache
@permission_required('timetable.add_dayplan')
def edit_calendar(request):
    qs = DayPlan.objects.filter(date__gte=date.today())
    if request.method == 'POST':
        formset = DayPlanFormSet(request.POST, queryset=qs)
        if formset.is_valid():
            formset.save()
            # Refresh the formset by refreshing the page
            return HttpResponseRedirect(request.path)
    else:
        formset = DayPlanFormSet(queryset=qs)
    context = {'formset': formset}
    return render(request, 'edit_calendar.html', context)

def show_rooms(request, date, period):
    date = parse_date(date)
    weekday = date.weekday()
    period = int(period)

    rooms = {room: None for room in Room.objects.all()}
    lessons = Lesson.objects.filter(weekday=weekday, period=period) \
        .select_related('room', 'teacher', 'group', 'subject')
    for lesson in lessons:
        rooms[lesson.room] = lesson

    substitutions = Substitution.objects.filter(date=date, lesson__period=period)
    for sub in substitutions:
        rooms[sub.lesson.room].substitute = sub.substitute

    context = {
        'date': date,
        'period': period,
        'rooms': rooms,
    }
    return render(request, 'rooms.html', context)

class RoomsDatePeriodSelectView(FormView):
    """A form with date and period to be passed to show_rooms."""
    template_name = 'rooms_date_period_select.html'
    form_class = SelectDateAndPeriodForm

    def form_valid(self, form):
        date = form.cleaned_data['date']
        period = form.cleaned_data['period']
        return redirect('rooms', date, period)

@never_cache
def display(request):
    context = get_display_context()
    return render(request, 'display.html', context)

@permission_required('timetable.add_substitution')
def delete_substitution(request, substitution_id):
    if request.POST:
        obj = get_object_or_404(Substitution, pk=substitution_id)
        obj.delete()
        return HttpResponseRedirect(reverse('add_substitutions1'))

class SubstitutionsImportView(FormView):
    template_name = 'import_substitutions.html'
    form_class = SubstitutionsImportForm
    permission_required = 'timetable.add_substitution'
    
    def form_valid(self,form):
        csv_file = TextIOWrapper(form.cleaned_data['file'],encoding="iso-8859-2")
        a = CSVReader(csv_file,delimiter=';')
        first_row = True
        for row in a:
            if first_row:
                first_row = False
                continue
            if len(row)<7:
                continue
            try:
                row_date = date(year=int(row[0][0:4]),
                                  month=int(row[0][5:7]),
                                  day=int(row[0][8:10]) )
                row_lesson = Lesson.objects.get(weekday=subst.date.weekday(),
                                                  period=int(row[1]),
                                                  teacher=get_teacher_by_name(row[4]))
                row_substitute = None
                if row[5]!='zajęcia odwołane':
                    row_substitute = get_teacher_by_name(row[5])

                #Check if substitution exist
                query = Substitution.objects.filter(date=row_date,
                                     lesson=row_lesson)
                #If substitution does not exist, add one
                if query.count() == 0:
                    subst = Substitution(date=row_date,
                                         lesson=row_lesson,
                                         substitute=row_substitute)
                    subst.save()
                #Otherwise update subsitute
                else:
                    subst = query.first()
                    subst.substitute=row_substitute
                    subst.save()

            except:
                pass
        return HttpResponseRedirect(reverse('add_substitutions1'))
