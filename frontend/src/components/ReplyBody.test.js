import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import ReplyBody from './ReplyBody';
import FileChanges from './FileChanges';
import AgentSteps from './AgentSteps';

// react-markdown ships as an ES module that Jest here can't load; what matters is what is handed to it
jest.mock('./Markdown', () => {
  const React = require('react');
  return { __esModule: true, default: ({ children }) => React.createElement('div', { className: 'md' }, children) };
});

const longCode = Array.from({ length: 12 }, (_, i) => `line_${i} = ${i}`).join('\n');

describe('a reply with code in it', () => {
  test('while it streams, code is replaced by a sign that says it is building', () => {
    render(<ReplyBody streaming content={`Here you go.\n\`\`\`python\n${longCode}`} />);
    expect(screen.getByText(/Here you go\./)).toBeTruthy();
    expect(screen.getByText('Building')).toBeTruthy();
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.queryByText(/line_3/)).toBeNull();
  });

  test('a block that is finished while the rest still streams reads as built, not building', () => {
    render(<ReplyBody streaming content={`\`\`\`python\n${longCode}\n\`\`\`\nand then`} />);
    expect(screen.getByText('Built')).toBeTruthy();
    expect(screen.queryByText('Building')).toBeNull();
  });

  test('when finished, a long block folds away until it is asked for', () => {
    render(<ReplyBody content={`\`\`\`python\n${longCode}\n\`\`\``} />);
    expect(screen.queryByText(/line_3/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /show code/ }));
    expect(screen.getByText(/line_3/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /hide code/ }));
    expect(screen.queryByText(/line_3/)).toBeNull();
  });

  test('when finished, a short snippet stays where it is', () => {
    render(<ReplyBody content={'try this:\n```python\nprint(1)\n```'} />);
    expect(screen.getByText(/print\(1\)/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /show code/ })).toBeNull();
  });
});

describe('the files a reply changed', () => {
  const files = [
    { path: '~/Desktop/app.py', status: 'created', added: 2, removed: 0, diff: '@@ -0,0 +1,2 @@\n+import os\n+print(1)' },
    { path: 'notes.md', status: 'modified', added: 1, removed: 1, diff: '@@ -1 +1 @@\n-before\n+after' },
    { path: 'logo.png', status: 'created', note: 'a file with no text to compare' },
  ];

  test('lists each file with its status and line counts, and the total', () => {
    render(<FileChanges files={files} />);
    expect(screen.getByText('3 files changed')).toBeTruthy();
    expect(screen.getByText('~/Desktop/app.py')).toBeTruthy();
    expect(screen.getByText('edited')).toBeTruthy();
    expect(screen.getAllByText('+3').length).toBeGreaterThan(0);
  });

  test('a click shows the diff, and another hides it; only one is open at a time', () => {
    render(<FileChanges files={files} />);
    expect(screen.queryByText('import os')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /app\.py/ }));
    expect(screen.getByText('import os')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /notes\.md/ }));
    expect(screen.queryByText('import os')).toBeNull();
    expect(screen.getByText('after')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /notes\.md/ }));
    expect(screen.queryByText('after')).toBeNull();
  });

  test('a file with nothing to compare says why', () => {
    render(<FileChanges files={files} />);
    fireEvent.click(screen.getByRole('button', { name: /logo\.png/ }));
    expect(screen.getByText('a file with no text to compare')).toBeTruthy();
  });
});

describe('code run by a step', () => {
  const step = { id: 1, tool: 'run_python', args: { code: 'print(1)\nprint(2)' }, status: 'ok', result: '1\n2' };

  test('is folded behind a line saying how much there is', () => {
    render(<AgentSteps steps={[step]} streaming={false} onDecide={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /1 step/ }));
    expect(screen.queryByText(/print\(2\)/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /show code · python · 2 lines/ }));
    expect(screen.getByText(/print\(2\)/)).toBeTruthy();
  });
});
