import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';
import ApiKeyModal from './ApiKeyModal';

jest.mock('axios', () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));
jest.mock('../App', () => ({ API_BASE: 'http://api' }));

const status = {
  provider: 'Ollama', provider_id: 'ollama', model: 'llama3.2:latest', has_key: false,
  providers: {
    openai: { has_key: false, masked_key: null, model: 'gpt-4o-mini' },
    anthropic: { has_key: false, masked_key: null, model: 'claude-haiku-5-5' },
    ollama: { has_key: false, model: 'llama3.2:latest' },
  },
};

// Answers each request the screen makes; `ollama` is what /settings/ollama-models says
function backend({ ollama, start }) {
  axios.get.mockImplementation((url) => {
    if (url.endsWith('/settings/api-key')) return Promise.resolve({ data: status });
    if (url.endsWith('/settings/ollama-models')) return Promise.resolve({ data: typeof ollama === 'function' ? ollama() : ollama });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  axios.post.mockImplementation((url) => {
    if (url.endsWith('/settings/ollama/start')) return start();
    return Promise.reject(new Error(`unexpected POST ${url}`));
  });
}

const stopped = { reachable: false, models: [], selected: 'llama3.2:latest' };
const running = { reachable: true, models: [{ name: 'llama3.2:latest', size_gb: 2 }], selected: 'llama3.2:latest' };

beforeEach(() => jest.resetAllMocks());

describe('the Ollama card', () => {
  test('when Ollama is not running it says so and offers to run it', async () => {
    backend({ ollama: stopped, start: () => Promise.resolve({ data: {} }) });
    render(<ApiKeyModal onClose={() => {}} onChanged={() => {}} />);
    expect(await screen.findByText("ollama isn't running")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'run ollama' })).toBeTruthy();
  });

  test('when it is running there is no button, only a note that it is', async () => {
    backend({ ollama: running, start: () => Promise.resolve({ data: {} }) });
    render(<ApiKeyModal onClose={() => {}} onChanged={() => {}} />);
    expect(await screen.findByText('ollama is running')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'run ollama' })).toBeNull();
  });

  test('pressing it starts Ollama, then shows it running with its models', async () => {
    let up = false;
    const onChanged = jest.fn();
    backend({
      ollama: () => (up ? running : stopped),
      start: () => { up = true; return Promise.resolve({ data: { running: true, started: true, code: 'started', message: 'ollama is running' } }); },
    });
    render(<ApiKeyModal onClose={() => {}} onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole('button', { name: 'run ollama' }));
    expect(await screen.findByText('ollama is running')).toBeTruthy();
    expect(axios.post).toHaveBeenCalledWith('http://api/settings/ollama/start');
    expect(screen.getByRole('combobox', { name: 'Ollama model' })).toBeTruthy();    // the models arrived
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'run ollama' })).toBeNull();
  });

  test('while it starts the button says so and cannot be pressed again', async () => {
    let finish;
    backend({ ollama: stopped, start: () => new Promise((resolve) => { finish = resolve; }) });
    render(<ApiKeyModal onClose={() => {}} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'run ollama' }));
    const pending = await screen.findByRole('button', { name: 'starting...' });
    expect(pending.disabled).toBe(true);
    finish({ data: { running: false, started: false, code: 'timeout', message: 'x' } });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'starting...' })).toBeNull());
  });

  test('when it cannot be started the reason is shown and the button stays', async () => {
    backend({
      ollama: stopped,
      start: () => Promise.resolve({ data: { running: false, started: false, code: 'not_installed',
        message: "Ollama isn't installed. get it from ollama.com, then press this again." } }),
    });
    render(<ApiKeyModal onClose={() => {}} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'run ollama' }));
    expect(await screen.findByText(/get it from ollama.com/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'run ollama' }).disabled).toBe(false);
    expect(screen.getByText("ollama isn't running")).toBeTruthy();
  });

  test('if the backend itself cannot be reached it says that, not nothing', async () => {
    backend({ ollama: stopped, start: () => Promise.reject(new Error('Network Error')) });
    render(<ApiKeyModal onClose={() => {}} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'run ollama' }));
    expect(await screen.findByText("couldn't reach the backend to start ollama")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'run ollama' }).disabled).toBe(false);
  });

  test('an earlier error is cleared when you try again', async () => {
    let attempt = 0;
    backend({
      ollama: stopped,
      start: () => { attempt += 1; return attempt === 1
        ? Promise.resolve({ data: { running: false, code: 'timeout', message: 'first failure' } })
        : new Promise(() => {}); },
    });
    render(<ApiKeyModal onClose={() => {}} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'run ollama' }));
    await screen.findByText('first failure');
    fireEvent.click(screen.getByRole('button', { name: 'run ollama' }));
    await waitFor(() => expect(screen.queryByText('first failure')).toBeNull());
  });
});
