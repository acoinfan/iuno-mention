// QQ 提醒的前端（丙类扩展，跑在沙盒小框里）：标签页右键菜单里加一项，点了打开设置对话框。发信本身走信箱（收件人「名字@qq」）。
await viewer.menu('tab.menu', [{ label: 'QQ 提醒设置', hint: '开关、静默时段、限额、发送记录', key: 'settings' }]);
viewer.on('menu', d => {
  if (d.key === 'settings') viewer.open('settings.html', { title: 'QQ 提醒' });
});
