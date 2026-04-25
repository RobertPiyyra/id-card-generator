"""
Keyboard Language Auto-Switch Feature
Automatically switches keyboard/input language based on template language
"""

import json
import logging

logger = logging.getLogger(__name__)

# ================== Keyboard Layout Mapping ==================
KEYBOARD_LANGUAGE_MAP = {
    'english': {
        'code': 'en-US',
        'name': 'English (US)',
        'layout': 'QWERTY',
        'rtl': False
    },
    'urdu': {
        'code': 'ur',
        'name': 'اردو',
        'layout': 'Urdu',
        'rtl': True
    },
    'arabic': {
        'code': 'ar',
        'name': 'العربية',
        'layout': 'Arabic',
        'rtl': True
    },
    'hindi': {
        'code': 'hi',
        'name': 'हिन्दी',
        'layout': 'Hindi',
        'rtl': False
    }
}


# ================== Input Method Helper Functions ==================
def get_keyboard_config(language):
    """
    Get keyboard configuration for a specific language.
    
    Args:
        language (str): Language name (e.g., 'english', 'urdu', 'arabic')
    
    Returns:
        dict: Keyboard configuration
    """
    lang_lower = (language or 'english').lower().strip()
    return KEYBOARD_LANGUAGE_MAP.get(lang_lower, KEYBOARD_LANGUAGE_MAP['english'])


def generate_keyboard_switcher_script(template_id, language):
    """
    Generate JavaScript code that auto-switches keyboard on input focus.
    This uses the Web API (on supported browsers) or falls back to hints.
    
    Args:
        template_id (int): Template ID
        language (str): Language for the template
    
    Returns:
        str: JavaScript code
    """
    keyboard_config = get_keyboard_config(language)
    keyboard_code = keyboard_config['code']
    keyboard_name = keyboard_config['name']
    rtl = keyboard_config['rtl']
    
    script = f"""
<script>
(function() {{
    'use strict';
    
    // ================== Auto Keyboard Switcher ==================
    // This script automatically switches input language/keyboard when form fields are focused
    
    const TEMPLATE_ID = {template_id};
    const TARGET_LANGUAGE = '{language}';
    const KEYBOARD_CODE = '{keyboard_code}';
    const KEYBOARD_NAME = '{keyboard_name}';
    const IS_RTL = {str(rtl).lower()};
    
    // Track current keyboard state
    let currentKeyboard = 'en-US';  // Default to English
    let previousKeyboard = 'en-US';
    let isAutoSwitchEnabled = true;
    
    /**
     * Helper: Check if browser supports input language API
     */
    function supportsInputLanguage() {{
        try {{
            return 'inputLanguage' in navigator || 'language' in navigator;
        }} catch (e) {{
            return false;
        }}
    }}
    
    /**
     * Attempt to switch input method on modern browsers (Android, some Windows builds)
     */
    function switchInputMethod() {{
        try {{
            // Try to use IME API if available
            if (navigator.inputLanguage) {{
                navigator.inputLanguage = KEYBOARD_CODE;
            }}
            
            // Try WebKit API (Chrome on Android)
            if (navigator.keyboard && navigator.keyboard.lock) {{
                navigator.keyboard.lock([KEYBOARD_CODE]).catch(e => {{
                    console.debug('Keyboard lock not supported:', e);
                }});
            }}
            
            console.debug(`Attempted keyboard switch to: ${{KEYBOARD_NAME}} (${{KEYBOARD_CODE}})`);
        }} catch (e) {{
            console.debug('Keyboard switch error:', e);
        }}
    }}
    
    /**
     * Fallback: Show language indicator in input field
     */
    function addLanguageIndicator(input) {{
        if (!input) return;
        
        // Remove any existing indicator
        const existingIndicator = input.parentElement.querySelector('.language-indicator');
        if (existingIndicator) existingIndicator.remove();
        
        // Create indicator
        const indicator = document.createElement('span');
        indicator.className = 'language-indicator';
        indicator.textContent = KEYBOARD_NAME;
        indicator.style.cssText = `
            position: absolute;
            bottom: 5px;
            right: 5px;
            font-size: 10px;
            background-color: #2196F3;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            z-index: 1000;
        `;
        
        // Parent must be relative for positioning
        const parent = input.parentElement;
        if (window.getComputedStyle(parent).position === 'static') {{
            parent.style.position = 'relative';
        }}
        
        parent.appendChild(indicator);
    }}
    
    /**
     * Setup input fields for auto-switching
     */
    function setupInputFields() {{
        const formInputs = document.querySelectorAll('input[type="text"], textarea, [contenteditable="true"]');
        
        formInputs.forEach((input) => {{
            // Add language indicator
            addLanguageIndicator(input);
            
            // On focus: Try to switch keyboard
            input.addEventListener('focus', function(e) {{
                if (isAutoSwitchEnabled) {{
                    switchInputMethod();
                    previousKeyboard = currentKeyboard;
                    currentKeyboard = KEYBOARD_CODE;
                    
                    // Show visual feedback
                    this.setAttribute('data-keyboard', KEYBOARD_CODE);
                    this.style.direction = IS_RTL ? 'rtl' : 'ltr';
                }};
            }});
            
            // Optional: On blur, revert to English (comment out if not needed)
            input.addEventListener('blur', function(e) {{
                if (isAutoSwitchEnabled && KEYBOARD_CODE !== 'en-US') {{
                    try {{
                        // Attempt to switch back to English
                        currentKeyboard = 'en-US';
                    }} catch (e) {{
                        console.debug('Keyboard revert error:', e);
                    }}
                }}
            }});
            
            // Set initial text direction
            input.style.direction = IS_RTL ? 'rtl' : 'ltr';
        }});
        
        console.info(`Keyboard auto-switch initialized for: ${{KEYBOARD_NAME}}`);
    }}
    
    /**
     * Toggle auto-switch on/off
     */
    window.toggleKeyboardAutoSwitch = function(enabled) {{
        isAutoSwitchEnabled = enabled;
        console.info(`Auto keyboard switch: ${{enabled ? 'ENABLED' : 'DISABLED'}}`);
    }};
    
    /**
     * Get current keyboard status
     */
    window.getKeyboardStatus = function() {{
        return {{
            enabled: isAutoSwitchEnabled,
            current: currentKeyboard,
            target: KEYBOARD_CODE,
            language: TARGET_LANGUAGE,
            rtl: IS_RTL
        }};
    }};
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', setupInputFields);
    }} else {{
        setupInputFields();
    }}
    
    // Also setup new inputs added dynamically
    const observer = new MutationObserver(function(mutations) {{
        mutations.forEach(function(mutation) {{
            if (mutation.addedNodes.length) {{
                mutation.addedNodes.forEach(function(node) {{
                    if (node.nodeType === 1) {{  // Element node
                        if (node.matches && node.matches('input[type="text"], textarea, [contenteditable="true"]')) {{
                            addLanguageIndicator(node);
                            node.addEventListener('focus', function() {{
                                if (isAutoSwitchEnabled) switchInputMethod();
                            }});
                            node.style.direction = IS_RTL ? 'rtl' : 'ltr';
                        }}
                    }}
                }});
            }}
        }});
    }});
    
    observer.observe(document.body, {{ childList: true, subtree: true }});
}})();
</script>
    """
    
    return script


def generate_keyboard_control_html(template_id, language):
    """
    Generate HTML UI control for toggling keyboard auto-switch.
    
    Args:
        template_id (int): Template ID
        language (str): Language
    
    Returns:
        str: HTML markup
    """
    keyboard_config = get_keyboard_config(language)
    keyboard_name = keyboard_config['name']
    
    html = f"""
<!-- Keyboard Auto-Switch Control Panel -->
<div class="keyboard-control-panel" style="
    background-color: #f5f5f5;
    border: 1px solid #ddd;
    border-radius: 5px;
    padding: 12px;
    margin-bottom: 15px;
    display: flex;
    align-items: center;
    justify-content: space-between;
">
    <div style="display: flex; align-items: center; gap: 10px;">
        <span style="font-size: 14px; color: #666;">
            <strong>⌨️ Keyboard:</strong> {keyboard_name}
        </span>
        <span id="keyboard-status" style="
            display: inline-block;
            background-color: #4CAF50;
            color: white;
            padding: 4px 8px;
            border-radius: 3px;
            font-size: 12px;
        ">
            Auto-Switch: ON
        </span>
    </div>
    
    <button id="toggle-keyboard-btn" type="button" style="
        background-color: #2196F3;
        color: white;
        border: none;
        padding: 8px 12px;
        border-radius: 3px;
        cursor: pointer;
        font-size: 12px;
    ">
        Disable Auto-Switch
    </button>
</div>

<script>
(function() {{
    const toggleBtn = document.getElementById('toggle-keyboard-btn');
    const statusSpan = document.getElementById('keyboard-status');
    let isEnabled = true;
    
    if (toggleBtn) {{
        toggleBtn.addEventListener('click', function(e) {{
            e.preventDefault();
            isEnabled = !isEnabled;
            
            // Toggle via the global function
            if (window.toggleKeyboardAutoSwitch) {{
                window.toggleKeyboardAutoSwitch(isEnabled);
            }}
            
            // Update UI
            toggleBtn.textContent = isEnabled ? 'Disable Auto-Switch' : 'Enable Auto-Switch';
            toggleBtn.style.backgroundColor = isEnabled ? '#2196F3' : '#f44336';
            
            statusSpan.textContent = 'Auto-Switch: ' + (isEnabled ? 'ON' : 'OFF');
            statusSpan.style.backgroundColor = isEnabled ? '#4CAF50' : '#f44336';
        }});
    }}
}})();
</script>
    """
    
    return html


def get_all_keyboard_languages():
    """
    Get list of all supported keyboard languages.
    
    Returns:
        list: List of dicts with language info
    """
    languages = []
    for lang_key, config in KEYBOARD_LANGUAGE_MAP.items():
        languages.append({
            'code': lang_key,
            'name': config['name'],
            'keyboard_code': config['code'],
            'layout': config['layout'],
            'rtl': config['rtl']
        })
    
    return languages


def validate_keyboard_language(language):
    """
    Validate if keyboard language is supported.
    
    Args:
        language (str): Language code
    
    Returns:
        bool: True if supported, False otherwise
    """
    return (language or 'english').lower() in KEYBOARD_LANGUAGE_MAP
