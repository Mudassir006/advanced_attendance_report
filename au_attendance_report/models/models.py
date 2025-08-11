from io import BytesIO
import base64
import xlwt

from odoo import models, fields, api
from datetime import date, timedelta, datetime, time

from odoo.exceptions import ValidationError
from odoo.tools import format_datetime


class AttendanceReportWizard(models.TransientModel):
    _name = 'attendance.report.wizard'
    _description = 'Attendance Report Wizard'

    year = fields.Selection(
        [(str(y), str(y)) for y in range(date.today().year, date.today().year - 5, -1)],
        string='Year', required=True, default=str(date.today().year)
    )
    month = fields.Selection(
        [(str(i), date(2000, i, 1).strftime('%B')) for i in range(1, 13)],
        string='Month'
    )
    date_from = fields.Date('Date From')
    date_to = fields.Date('Date To')
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True)
    show_leaves = fields.Boolean(string='Show Leaves',default=True)
    show_remaining_leaves = fields.Boolean(string='Show Remaining Leaves')

    @api.onchange('month', 'year')
    def _onchange_month(self):
        if self.month:
            year = int(self.year)
            month = int(self.month)
            self.date_from = date(year, month, 1)
            if month == 12:
                self.date_to = date(year, 12, 31)
            else:
                self.date_to = date(year, month + 1, 1) - timedelta(days=1)

            print(f"Attendance Report Wizard: Month and Year Changed. "
                  f"Setting date_from to {self.date_from} and date_to to {self.date_to}")

    @api.constrains('date_from', 'date_to')
    def check_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValidationError("Date From must be earlier than Date To.")
        elif self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError("Date To must be later than Date From.")
        elif self.date_to > date.today():
            raise ValidationError("Date To cannot be in the future.")
        elif self.date_from > date.today():
            raise ValidationError("Date From cannot be in the future.")

    def _get_attendance_data(self):
        result = []
        employee = self.employee_id
        total_worked_hours = 0
        total_planned_hours = 0
        # set it true because of change in initial logic
        self.show_leaves=True
        total_late_hours = 0
        total_overtime = 0
        total_absent_days = 0
        calendar = employee.resource_calendar_id

        if not self.date_from or not self.date_to:
            return result, {
                'summary_worked_hours': 0,
                'summary_planned_hours': 0,
                'summary_absent_days': 0,
                'summary_late_hours': 0,
                'summary_difference': 0,
                'summary_overtime': 0,
            }

        leaves = []
        if self.show_leaves:
            leave_obj = self.env['hr.leave']
            leaves = leave_obj.search([
                ('employee_id', '=', employee.id),
                ('state', '=', 'validate'),
                ('request_date_from', '<=', self.date_to),
                ('request_date_to', '>=', self.date_from),
            ])

        # Create a date range for the report
        date_range = (self.date_from + timedelta(days=i) for i in range((self.date_to - self.date_from).days + 1))
        working_days = 0

        for single_date in date_range:
            start_dt = datetime.combine(single_date, time.min)
            end_dt = datetime.combine(single_date, time.max)
            has_attendance = self.env['hr.attendance'].search_count([
                ('employee_id', '=', employee.id),
                ('check_in', '>=', start_dt),
                ('check_in', '<=', end_dt),
            ]) > 0

            # Check if the day is a weekend (Saturday/Sunday) could be done differently for different time zone
            if single_date.weekday() in [5, 6] and not has_attendance:
                result.append({
                    'date': single_date.strftime('%Y-%m-%d'),
                    'day': single_date.strftime('%A'),
                    'actual_hours': 0,
                    'check_in': '-',
                    'check_out': '-',
                    'worked_hours': 'Holiday',
                    'late_hours': 0,
                    'difference': 0,
                    'overtime': 0,
                    'absent': False,
                })
                continue

            on_leave = any(leave.request_date_from <= single_date <= leave.request_date_to for leave in leaves)
            total_planned_hours += calendar.hours_per_day
            if on_leave:
                result.append({
                    'date': single_date.strftime('%Y-%m-%d'),
                    'day': single_date.strftime('%A'),
                    'actual_hours': calendar.hours_per_day,
                    'check_in': 'On Leave',
                    'check_out': 'On Leave',
                    'worked_hours': 'Leave',
                    'late_hours': 0,
                    'difference': 0,
                    'overtime': 0,
                    'absent': False,
                })
                continue

            day_attendance = employee.attendance_ids.filtered(lambda a: a.check_in.date() == single_date)

            if day_attendance:
                check_in = min(day_attendance.mapped('check_in'))
                check_out = max(day_attendance.mapped('check_out'))
                worked_hours = round(sum(day_attendance.mapped('worked_hours')), 2)

                planned_hours = calendar.hours_per_day
                late_hours = 0
                difference = round((planned_hours - worked_hours),2)
                overtime = max(0, worked_hours - planned_hours)

                total_worked_hours += worked_hours
                total_overtime += overtime
                total_late_hours += late_hours

                result.append({
                    'date': single_date.strftime('%Y-%m-%d'),
                    'day': single_date.strftime('%A'),
                    'actual_hours': planned_hours,
                    'check_in': format_datetime(self.env, check_in),
                    'check_out': format_datetime(self.env, check_out),
                    'worked_hours': worked_hours,
                    'late_hours': late_hours,
                    'difference': difference,
                    'overtime': overtime,
                    'absent': False,
                })
            else:
                total_absent_days += 1
                result.append({
                    'date': single_date.strftime('%Y-%m-%d'),
                    'day': single_date.strftime('%A'),
                    'actual_hours': calendar.hours_per_day,
                    'check_in': 'Absent',
                    'check_out': 'Absent',
                    'worked_hours': 'Absent',
                    'late_hours': 0,
                    'difference': calendar.hours_per_day,
                    'overtime': 0,
                    'absent': True,
                })

        if self.show_remaining_leaves:
            leave_allocation = self.env['hr.leave.allocation'].search([
                ('employee_id', '=', employee.id),
                ('holiday_status_id.allocation_type', '=', 'fixed'),
                ('state', '=', 'validate'),
            ])
            total_allocated = sum(leave_allocation.mapped('number_of_days_display'))
            total_taken = sum(leaves.mapped('number_of_days'))
            remaining = total_allocated - total_taken

            result.append({
                'date': '',
                'day': '',
                'actual_hours': '',
                'check_in': '',
                'check_out': '',
                'worked_hours': '',
                'late_hours': '',
                'difference': '',
                'overtime': '',
                'absent': False,
                'note': f'Remaining Leaves: {remaining:.2f} Days'
            })

        return result, {
            'summary_worked_hours': round(total_worked_hours, 2),
            'summary_planned_hours': round(total_planned_hours, 2),
            'summary_absent_days': total_absent_days,
            'summary_late_hours': round(total_late_hours, 2),
            'summary_difference': round(total_planned_hours - total_worked_hours, 2),
            'summary_overtime': round(total_overtime, 2),
        }

    def action_print_report(self):
        print(f"Attendance Report Wizard: Generating report for Employee ID {self.employee_id.id}. "
              f"From {self.date_from} to {self.date_to}. Show Leaves: {self.show_leaves}, "
              f"Show Remaining Leaves: {self.show_remaining_leaves}")

        data = {
            'employee_id': self.employee_id.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'show_leaves': self.show_leaves,
            'show_remaining_leaves': self.show_remaining_leaves,
        }
        return self.env.ref('au_attendance_report.attendance_report_action').report_action(self, data=data)

    def generate_xls_report(self):

        holiday_style = xlwt.easyxf('font: colour_index black;')
        absent_style = xlwt.easyxf('font: colour_index red;')
        default_style = xlwt.easyxf('font: colour_index green')


        workbook = xlwt.Workbook()
        sheet = workbook.add_sheet(f'{self.employee_id.name}')
        col_widths = [15, 15, 15, 20, 20, 15, 15, 15, 15]
        for col_index, width in enumerate(col_widths):
            sheet.col(col_index).width = 256 * width  # 256 = 1 character width

        title_style = xlwt.easyxf('font: bold 1, height 280; align: horiz center')
        subtitle_style = xlwt.easyxf('font: bold 1; align: horiz left')
        header_style = xlwt.easyxf('font: bold 1; borders: bottom thin')

        sheet.write_merge(0, 0, 0, 8, 'Attendance Report', title_style)
        sheet.write_merge(1, 1, 0, 8, f'Employee: {self.employee_id.name}', subtitle_style)
        sheet.write_merge(2, 2, 0, 8, f'Period: {self.date_from} to {self.date_to}', subtitle_style)
        row = 4

        headers = [
            'Date', 'Day', 'Actual Hours', 'Check-in', 'Check-out', 'Worked Hours',
            'Late Hours', 'Difference', 'Overtime'
        ]
        for col, header in enumerate(headers):
            sheet.write(row, col, header, header_style)
        row += 1

        attendance_lines, summary = self._get_attendance_data()  # Unpack the tuple it returns a list of tuple so first unpack and than iterate over it

        for line in attendance_lines:
            if isinstance(line, dict):

                if line.get('absent'):
                    text_style = absent_style
                elif line.get('worked_hours') == 'Holiday':
                    text_style = holiday_style
                else:
                    text_style = default_style

                sheet.write(row, 0, line.get('date'), text_style)
                sheet.write(row, 1, line.get('day'), text_style)
                sheet.write(row, 2, line.get('actual_hours'), text_style)
                sheet.write(row, 3, line.get('check_in'), text_style)
                sheet.write(row, 4, line.get('check_out'), text_style)
                sheet.write(row, 5, line.get('worked_hours'), text_style)
                sheet.write(row, 6, line.get('late_hours'), text_style)
                sheet.write(row, 7, line.get('difference'), text_style)
                sheet.write(row, 8, line.get('overtime'), text_style)
                row += 1

        summary_title_style = xlwt.easyxf('font: bold 1, height 220; align: horiz center; borders: bottom thick')
        summary_label_style = xlwt.easyxf(
            'font: bold 1; borders: left thin, right thin, top thin, bottom thin; align: horiz left')
        summary_value_style = xlwt.easyxf('borders: left thin, right thin, top thin, bottom thin; align: horiz right')
        summary_start_col = 10
        summary_start_row = 4
        sheet.write_merge(summary_start_row, summary_start_row, summary_start_col, summary_start_col + 1, 'Summary',
                          summary_title_style)

        summary_data = [
            ('Total Worked Hours', summary['summary_worked_hours']),
            ('Total Planned Hours', summary['summary_planned_hours']),
            ('Total Absent Days', summary['summary_absent_days']),
            ('Total Late Hours', summary['summary_late_hours']),
            ('Total Difference', summary['summary_difference']),
            ('Total Overtime', summary['summary_overtime']),
        ]


        for idx, (label, value) in enumerate(summary_data):
            sheet.col(summary_start_col).width = 256 * 25
            sheet.col(summary_start_col + 1).width = 256 * 15
            sheet.write(summary_start_row + idx + 1, 10, label,summary_label_style)
            sheet.write(summary_start_row + idx + 1, 11, value,summary_value_style)

        xls_data = BytesIO()
        workbook.save(xls_data)
        xls_data.seek(0)

        return self.env['ir.attachment'].create({
            'name': 'Attendance Report.xls',
            'type': 'binary',
            'datas': base64.b64encode(xls_data.read()).decode('utf-8'),
            'mimetype': 'application/vnd.ms-excel',
        })

    def action_export_xls(self):
        attachment = self.generate_xls_report()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'new',
        }


class AttendanceReport(models.AbstractModel):
    _name = 'report.attendance_report.attendance_report_template'
    _description = 'Attendance Report Template'

    def _get_report_values(self, docids, data=None):
        employee = self.env['hr.employee'].browse(data.get('employee_id'))
        date_from = data.get('date_from')
        date_to = data.get('date_to')
        show_leaves = data.get('show_leaves', False)
        show_remaining_leaves = data.get('show_remaining_leaves', False)

        print(f"Attendance Report: Generating report values for Employee: {employee.name}, "
              f"From: {date_from}, To: {date_to}, Show Leaves: {show_leaves}, Show Remaining Leaves: {show_remaining_leaves}")

        Wizard = self.env['attendance.report.wizard']
        wizard = Wizard.create({
            'employee_id': employee.id,
            'date_from': date_from,
            'date_to': date_to,
            'show_leaves': show_leaves,
            'show_remaining_leaves': show_remaining_leaves,
        })

        attendance_lines, summary = wizard._get_attendance_data()
        print(f'kkkkkkkkkkkkkkkkkkk{attendance_lines}')

        return {
            'doc_ids': [wizard.id],
            'doc_model': 'attendance.report.wizard',
            'doc': wizard,
            'attendance_lines': attendance_lines,
            'employee_name': employee.name,
            'date_from': date_from,
            'date_to': date_to,
            **summary,
        }