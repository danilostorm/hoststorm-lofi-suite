from pathlib import Path

HTML = Path('/app/templates/index.html')

h = HTML.read_text(encoding='utf-8')
old_button = '<button type="submit">+ Agendar</button>'
platforms_marker = '<div class="schedule-platforms">'

button_pos = h.find(old_button)
platform_pos = h.find(platforms_marker)

# Remove apenas o botão antigo que ficou antes do bloco de plataformas.
if button_pos != -1 and platform_pos != -1 and button_pos < platform_pos:
    h = h[:button_pos] + h[button_pos + len(old_button):]

HTML.write_text(h, encoding='utf-8')
print('Patch v4 aplicado: botão duplicado removido.')
