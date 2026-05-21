"""LLM settings route handlers."""
import json
from flask import Blueprint, jsonify, redirect, request
from ..helpers import _llm_settings_file, _load_llm_settings
import requests

from backlink_publisher.persistence.safe_write import atomic_write

bp = Blueprint("llm", __name__)


_LLM_DEFAULTS = {
    'api_key': '',
    'endpoint': '',
    'model': '',
    'temperature': 0.7,
    'system_prompt': '',
    'use_article_gen': False,
    'article_system_prompt': '',
    'image_gen_api_key': '',
    'use_image_gen': False,
}


def _write_llm_settings(payload: dict) -> None:
    # Delegates to the canonical credential-write helper so the file lands
    # 0o600 (api_key is a long-term secret). PR #139 hand-rolled this write
    # and forgot the chmod, leaving llm-settings.json world-readable.
    path = _llm_settings_file()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write(path, text)


@bp.route('/settings/save-llm-config', methods=['POST'])
def settings_save_llm_config():
    # P2: clearLlmSettings() flips a hidden action=clear marker to reset the whole file.
    if request.form.get('action') == 'clear':
        try:
            _write_llm_settings(dict(_LLM_DEFAULTS))
            return redirect('/settings?flash_type=success&flash_msg=LLM 配置已清除#sect-ai')
        except Exception as e:
            return redirect(f'/settings?flash_type=danger&flash_msg=清除失败: {e}#sect-ai')

    existing = _load_llm_settings()
    try:
        temperature = float(request.form.get('temperature', existing.get('temperature', 0.7)))
    except ValueError:
        temperature = existing.get('temperature', 0.7)

    # P3: blank secret inputs preserve the stored value so we don't wipe it on partial edits.
    new_api_key = request.form.get('api_key', '').strip()
    new_image_key = request.form.get('image_gen_api_key', '').strip()

    existing.update({
        'endpoint': request.form.get('endpoint', '').strip().rstrip('/'),
        'api_key': new_api_key or existing.get('api_key', ''),
        'model': request.form.get('model', '').strip(),
        'temperature': temperature,
        'system_prompt': request.form.get('system_prompt', ''),
        'use_article_gen': 'use_article_gen' in request.form,
        'image_gen_api_key': new_image_key or existing.get('image_gen_api_key', ''),
        'use_image_gen': 'use_image_gen' in request.form,
    })
    try:
        _write_llm_settings(existing)
        return redirect('/settings?flash_type=success&flash_msg=LLM 设定已保存#sect-ai')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}#sect-ai')

@bp.route('/settings/test-llm-connection', methods=['POST'])
def settings_test_llm():
    try:
        endpoint = request.form.get('endpoint', '').strip().rstrip('/')
        api_key = request.form.get('api_key', '').strip()
        model = request.form.get('model', '').strip()

        # P3 fallback: form sends blanks when secrets aren't re-typed; read stored values.
        if not api_key or not endpoint:
            stored = _load_llm_settings()
            api_key = api_key or stored.get('api_key', '')
            endpoint = endpoint or stored.get('endpoint', '').rstrip('/')
            model = model or stored.get('model', '')

        if not endpoint or not api_key:
            return jsonify({'status': 'error', 'message': '请填写 Endpoint 和 API Key'}), 200

        # Try to call v1/models
        test_url = f"{endpoint}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        models_list = []
        try:
            resp = requests.get(test_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                try:
                    m_data = resp.json()
                    if isinstance(m_data, dict) and 'data' in m_data:
                        models_list = [m['id'] for m in m_data['data'] if isinstance(m, dict) and 'id' in m]
                except Exception:
                    pass
                return jsonify({'status': 'ok', 'message': '连接成功！', 'models': models_list}), 200
            
            # Fallback
            test_url = f"{endpoint}/chat/completions"
            data = {"model": model or "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
            resp = requests.post(test_url, headers=headers, json=data, timeout=10)
            if resp.status_code == 200:
                return jsonify({'status': 'ok', 'message': '连接成功！', 'models': []}), 200
            
            return jsonify({'status': 'error', 'message': f'连接失败: HTTP {resp.status_code}'}), 200
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'请求异常: {str(e)}'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'发生错误: {str(e)}'}), 200

@bp.route('/settings/test-llm-generation', methods=['POST'])
def settings_preview_llm():
    try:
        from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider
        settings = _load_llm_settings()
        
        provider = OpenAICompatibleProvider(
            base_url=settings['endpoint'],
            api_key=settings['api_key'],
            model=settings['model'],
            temperature=settings['temperature'],
            system_prompt=settings['system_prompt'],
            article_system_prompt=settings['article_system_prompt']
        )
        
        test_title = request.form.get('test_title', '测试文章')
        test_content = request.form.get('test_content', '这是一个测试内容。')
        
        if settings.get('use_article_gen'):
            result = provider.generate_article_body(
                domain_label='example.com',
                main_domain='https://example.com',
                anchors=['示例锚点', '更多资源'],
                topic=test_title
            )
            return jsonify({'status': 'ok', 'result': result}), 200
        else:
            # Fallback to anchor candidate generation
            from backlink_publisher.publishing.adapters.llm_anchor_provider import LLMAnchorRequest
            req = LLMAnchorRequest(keyword=test_title, domain="example.com", target_url="https://example.com")
            result = provider.generate_candidates(req)
            return jsonify({'status': 'ok', 'result': f"生成的锚点候选: {', '.join(result)}"}), 200
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'生成预览失败: {str(e)}'}), 200
